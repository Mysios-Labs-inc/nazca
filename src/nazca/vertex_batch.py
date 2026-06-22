"""Vertex AI Batch inference for Gemini image models (Tier 2D).

The async escape hatch from the 2 req/min online wall. Confirmed eligible
against Google Cloud docs (last updated 2026-06-22): **Gemini 3 Pro Image** and
**Gemini 3.1 Flash Image** (and 2.5 Flash Image) support batch inference,
including via the **global endpoint**. Properties:

- **No predefined per-minute quota** — a large shared pool, dynamically allocated.
- **50% cheaper** than online inference.
- Async: submit a JSONL of requests → poll → fetch predictions from GCS. Up to
  200k requests/job; jobs run within ~24h (after up to 72h queue).
- **Image output is capped at 1K** in batch — 2K/4K are NOT supported. We force
  1K and warn if a row asked for more.

Lifecycle: build per-row request JSONL → upload to GCS → create a
`batchPredictionJob` (one per model) → poll to SUCCEEDED → read predictions from
the job's GCS output dir → decode images back to each row's `out`.

NOTE: not yet validated against a live batch job (no probe project available at
build time). API shapes follow the documented batchPredictionJobs + GenAI batch
request/response format. The pure logic (request building, output correlation,
1K enforcement, GCS URI handling) is unit-tested; live HTTP is behind injectable
seams.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from nazca import config, retry
from nazca.image import _gemini_body, _gemini_extract, _resolve
from nazca.vertex import VertexError, access_token

# Gemini image models documented as batch-eligible (2026-06-22). Others may work
# but we warn so callers know they're off the verified list.
BATCH_IMAGE_MODELS = frozenset(
    {"gemini-3-pro-image", "gemini-3.1-flash-image", "gemini-2.5-flash-image"}
)

# Batch image output is 1K-only; we coerce and surface this.
BATCH_IMAGE_SIZE = "1K"

# Terminal job states.
_DONE = "JOB_STATE_SUCCEEDED"
_FAILED = {"JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}

# Per-request timeout (s) for GCS / job-status HTTP so a hung connection can't
# block a poll loop forever.
_HTTP_TIMEOUT = 120


class VertexBatchError(VertexError):
    """Raised for Vertex batch job/config problems."""


# --------------------------------------------------------------------------- GCS helpers
def parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Split gs://bucket/path/to/obj → ('bucket', 'path/to/obj')."""
    if not uri.startswith("gs://"):
        raise VertexBatchError(f"not a gs:// URI: {uri}")
    rest = uri[len("gs://") :]
    bucket, _, path = rest.partition("/")
    if not bucket:
        raise VertexBatchError(f"gs:// URI missing bucket: {uri}")
    return bucket, path


def _gcs_upload_text(uri: str, text: str, token: str) -> None:
    bucket, obj = parse_gcs_uri(uri)
    url = (
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
        f"?uploadType=media&name={urllib.parse.quote(obj, safe='')}"
    )
    req = urllib.request.Request(
        url,
        data=text.encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/x-ndjson"},
        method="POST",
    )
    _send(req, f"GCS upload {uri}")


def _gcs_list(prefix_uri: str, token: str) -> list[str]:
    """Return gs:// URIs of objects under a prefix."""
    bucket, prefix = parse_gcs_uri(prefix_uri)
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket}/o"
        f"?prefix={urllib.parse.quote(prefix, safe='')}"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    data = json.loads(_send(req, f"GCS list {prefix_uri}"))
    return [f"gs://{bucket}/{item['name']}" for item in data.get("items", [])]


def _gcs_download_bytes(uri: str, token: str) -> bytes:
    bucket, obj = parse_gcs_uri(uri)
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
        f"{urllib.parse.quote(obj, safe='')}?alt=media"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    return _send_bytes(req, f"GCS download {uri}")


def _send(req: urllib.request.Request, what: str) -> str:
    return _send_bytes(req, what).decode()


def _send_bytes(req: urllib.request.Request, what: str) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310 (trusted Google endpoint)
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        raise VertexBatchError(f"{what} failed: HTTP {e.code} {detail}") from e


# --------------------------------------------------------------------------- batch job REST
def _aiplatform_host(location: str) -> str:
    return "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"


def _submit_job(model_id: str, location: str, input_uri: str, output_prefix: str, token: str) -> str:
    """Create a batchPredictionJob; return its resource name."""
    if not config.VERTEX_PROJECT:
        raise VertexBatchError("VERTEX_PROJECT is not set — point nazca at your GCP project.")
    host = _aiplatform_host(location)
    url = (
        f"https://{host}/v1/projects/{config.VERTEX_PROJECT}/locations/{location}/batchPredictionJobs"
    )
    body = {
        "displayName": f"nazca-batch-{model_id}",
        "model": f"publishers/google/models/{model_id}",
        "inputConfig": {"instancesFormat": "jsonl", "gcsSource": {"uris": [input_uri]}},
        "outputConfig": {"predictionsFormat": "jsonl", "gcsDestination": {"outputUriPrefix": output_prefix}},
    }
    resp = retry.post_json(
        url,
        body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        on_http_error=lambda code, detail: VertexBatchError(f"submit job HTTP {code}: {detail}"),
        on_rate_limited=lambda code, detail: VertexBatchError(f"submit job rate-limited HTTP {code}: {detail}"),
    )
    name = resp.get("name")
    if not name:
        raise VertexBatchError(f"batch job create returned no name: {json.dumps(resp)[:300]}")
    return name


def _get_job(job_name: str, location: str, token: str) -> dict:
    host = _aiplatform_host(location)
    url = f"https://{host}/v1/{job_name}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    return json.loads(_send(req, f"get job {job_name}"))


# --------------------------------------------------------------------------- request building
def request_key(request: dict) -> str:
    """Stable hash of a request body, used to map an output line back to its row."""
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


@dataclass
class VertexBatchJob:
    model_id: str
    location: str
    rows: list  # list[BatchRow]
    input_uri: str
    output_prefix: str
    request_lines: list[dict] = field(default_factory=list)
    # request_key → FIFO queue of out paths (handles duplicate identical requests)
    key_to_outs: dict = field(default_factory=dict)


def build_request_line(row, force_size: str = BATCH_IMAGE_SIZE) -> dict:
    """Build one GenAI-batch input line: {"request": <generateContent body>}.

    Reuses image._gemini_body so the request matches the online path exactly,
    except `size` is forced to 1K (batch image output is 1K-only).
    """
    refs = list(row.refs or [])
    body = _gemini_body(row.prompt, refs, row.aspect, force_size)
    return {"request": body}


def warn_oversize_rows(rows) -> list:
    """Return rows that asked for >1K (batch can't honor it). Caller may warn."""
    return [r for r in rows if r.size and r.size.upper() in ("2K", "4K")]


# --------------------------------------------------------------------------- planning
def plan_vertex_jobs(rows, gcs_prefix: str) -> list[VertexBatchJob]:
    """Group pending rows by resolved Gemini model into one job each.

    Skips rows whose `out` exists (idempotent/resumable), rejects non-Gemini
    backends, and lays out per-model input/output GCS paths under gcs_prefix.
    """
    prefix = gcs_prefix.rstrip("/")
    if not prefix.startswith("gs://"):
        raise VertexBatchError(f"--gcs must be a gs:// prefix, got: {gcs_prefix}")

    grouped: dict[tuple[str, str], list] = {}
    for row in rows:
        if row.out.exists():
            continue  # resumable: already done
        model_id, location, api, backend = _resolve(row.model)
        if backend != "vertex" or api != "gemini":
            raise VertexBatchError(
                f"--vertex-batch only supports Vertex Gemini image models; "
                f"row '{row.out}' resolves to {backend}/{api} ({model_id})"
            )
        grouped.setdefault((model_id, location), []).append(row)

    jobs: list[VertexBatchJob] = []
    for (model_id, location), group in grouped.items():
        safe = model_id.replace("/", "_")
        job = VertexBatchJob(
            model_id=model_id,
            location=location,
            rows=group,
            input_uri=f"{prefix}/{safe}/input.jsonl",
            output_prefix=f"{prefix}/{safe}/output/",
        )
        for row in group:
            line = build_request_line(row)
            job.request_lines.append(line)
            job.key_to_outs.setdefault(request_key(line["request"]), []).append(row.out)
        jobs.append(job)
    return jobs


# --------------------------------------------------------------------------- output mapping
def _line_response(line: dict) -> dict | None:
    """Pull the GenerateContentResponse out of an output line.

    Vertex batch has used both `response` (GenAI batch) and `prediction`
    (classic predictionsFormat) — accept either so a field-name difference can't
    silently drop every image.
    """
    resp = line.get("response")
    if resp is None:
        resp = line.get("prediction")
    return resp if isinstance(resp, dict) else None


def _correlate(job: VertexBatchJob, output_lines: list[dict]) -> list:
    """Map each output line to a target `out` path.

    Primary strategy is **input order** — Vertex batch preserves the order of
    input lines, and this doesn't depend on the service echoing our exact JSON
    (refs are base64-inlined, so any re-encoding would defeat a hash match). When
    *every* line carries a `request` we recognize by hash, we trust the hash
    instead (robust to reordering). Returns a target list parallel to output_lines
    (None where a line can't be placed).
    """
    by_hash = all(
        isinstance(ln.get("request"), dict) and request_key(ln["request"]) in job.key_to_outs
        for ln in output_lines
    )
    if output_lines and by_hash:
        pending = {k: list(v) for k, v in job.key_to_outs.items()}
        targets = []
        for ln in output_lines:
            outs = pending.get(request_key(ln["request"])) or []
            targets.append(outs.pop(0) if outs else None)
        return targets
    # positional: output line i ↔ input row i
    ordered = [r.out for r in job.rows]
    return [ordered[i] if i < len(ordered) else None for i in range(len(output_lines))]


def _map_outputs(job: VertexBatchJob, output_lines: list[dict]) -> tuple[int, list[str]]:
    """Decode each output line's image to the matching row's `out`. Returns
    (written_count, errors)."""
    targets = _correlate(job, output_lines)
    written = 0
    errors: list[str] = []
    for line, target in zip(output_lines, targets):
        resp = _line_response(line)
        if resp is None:
            errors.append(f"{target or '?'}: no response/prediction ({json.dumps(line)[:200]})")
            continue
        if target is None:
            errors.append("output line did not match any input row")
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_gemini_extract(resp))
            written += 1
        except Exception as e:  # one bad line must not sink the fetch
            errors.append(f"{target}: {e}")
    # rows with no corresponding output line at all (short/failed shard)
    placed = sum(1 for t in targets if t is not None)
    missing = len(job.rows) - placed
    if missing > 0:
        errors.append(f"{missing} row(s) had no output line returned")
    return written, errors


def read_output_lines(output_prefix: str, token: str) -> list[dict]:
    """Fetch + parse all prediction JSONL lines under a job's output prefix."""
    lines: list[dict] = []
    for uri in _gcs_list(output_prefix, token):
        if not uri.endswith((".jsonl", ".ndjson")) and "prediction" not in uri:
            continue
        raw = _gcs_download_bytes(uri, token).decode()
        for ln in raw.splitlines():
            ln = ln.strip()
            if ln:
                lines.append(json.loads(ln))
    return lines


# --------------------------------------------------------------------------- orchestrator
def run_vertex_batch(
    rows,
    gcs_prefix: str,
    *,
    dry_run: bool = False,
    poll_interval: int | None = None,
    poll_max_tries: int | None = None,
    token_fn=access_token,
    on_event=None,
    _sleep=time.sleep,
) -> dict:
    """Submit one batch job per model, poll to completion, write images back.

    Returns a summary dict. `on_event(stage, detail)` reports progress.
    """
    on_event = on_event or (lambda *a: None)
    poll_interval = poll_interval if poll_interval is not None else config.POLL_INTERVAL
    poll_max_tries = poll_max_tries if poll_max_tries is not None else config.POLL_MAX_TRIES

    jobs = plan_vertex_jobs(rows, gcs_prefix)
    oversize = warn_oversize_rows([r for j in jobs for r in j.rows])
    pending = sum(len(j.rows) for j in jobs)

    summary: dict = {
        "jobs": len(jobs),
        "pending": pending,
        "oversize_forced_1k": len(oversize),
        "models": sorted({j.model_id for j in jobs}),
    }

    if dry_run:
        summary["planned"] = [
            {
                "model": j.model_id,
                "location": j.location,
                "rows": len(j.rows),
                "input_uri": j.input_uri,
                "output_prefix": j.output_prefix,
                "sample_request": _summarize_request(j.request_lines[0]) if j.request_lines else None,
            }
            for j in jobs
        ]
        return summary

    if not jobs:
        summary["written"] = 0
        return summary

    token = token_fn()
    written_total = 0
    all_errors: list[str] = []
    for job in jobs:
        on_event("upload", job)
        _gcs_upload_text(job.input_uri, "\n".join(json.dumps(ln) for ln in job.request_lines), token)
        on_event("submit", job)
        job_name = _submit_job(job.model_id, job.location, job.input_uri, job.output_prefix, token)
        state = _poll(job_name, job.location, token, poll_interval, poll_max_tries, on_event, _sleep)
        if state != _DONE:
            all_errors.append(f"{job.model_id}: job ended {state}")
            continue
        on_event("fetch", job)
        out_lines = read_output_lines(job.output_prefix, token)
        written, errors = _map_outputs(job, out_lines)
        written_total += written
        all_errors.extend(errors)

    summary["written"] = written_total
    summary["errors"] = all_errors
    return summary


def _poll(job_name, location, token, interval, max_tries, on_event, _sleep) -> str:
    for _ in range(max_tries):
        job = _get_job(job_name, location, token)
        state = job.get("state", "")
        on_event("poll", state)
        if state == _DONE or state in _FAILED:
            return state
        _sleep(interval)
    raise VertexBatchError(f"batch job {job_name} did not finish after {max_tries} polls")


def _summarize_request(line: dict) -> dict:
    """Trim base64 ref blobs from a request line for readable dry-run output."""
    req = line.get("request", {})
    parts = req.get("contents", [{}])[0].get("parts", [])
    trimmed = [
        ({"inlineData": f"<{len(p['inlineData']['data'])} b64>"} if "inlineData" in p else p)
        for p in parts
    ]
    cfg = req.get("generationConfig", {})
    return {"parts": trimmed, "generationConfig": cfg}
