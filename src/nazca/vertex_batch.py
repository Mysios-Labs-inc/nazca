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

Lifecycle: upload each ref to GCS once → build per-row request JSONL (refs by
fileData/gcsUri, not inlined base64) → upload to GCS → create a
`batchPredictionJob` (one per model) → poll to SUCCEEDED → stream every
prediction shard from the job's GCS output dir → decode images back to each
row's `out`.

Correlation (the production-correctness fix): Vertex Batch returns predictions
**out of input order** and echoes each request with extra/normalized fields, so
mapping a prediction back to its row by *position* (or by a full-body request
hash) silently writes every image to the wrong `out` path and mislabels which row
was safety-filtered. We instead correlate by `request_signature` — the prompt text
plus ref URIs that Vertex passes through verbatim — and treat an unmatched line as
an error rather than guessing a position. See `_OutputSink`.

Scale (the production fix): a live 12-image job emitted a 109 MB
predictions.jsonl that a single read-all-into-RAM download TimeoutError'd on; 840
images would be multi-GB. So predictions are (a) downloaded chunk-by-chunk to a
temp file with a large socket-read timeout, (b) stream-parsed line-by-line with
each image decoded straight to disk, (c) read across ALL output shards
(predictions.jsonl-00000-of-000NN), and (d) shrunk ~4× by referencing refs via
fileData/gcsUri so Vertex's per-line request echo is tiny. Peak memory is one
line + one image at any job size.

NOTE: not yet validated against a live batch job (no probe project available at
build time). API shapes follow the documented batchPredictionJobs + GenAI batch
request/response format. The pure logic (request building, streaming output
correlation, sharding, 1K enforcement, GCS URI handling) is unit-tested; live
HTTP is behind injectable seams.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from nazca import config, retry
from nazca.backends.vertex import VertexError, access_token, gemini_extract
from nazca.image import _resolve
from nazca.media import encode_image_b64

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

# Control-plane timeout (s): submit / poll / list / upload are small calls, so a
# hung connection there shouldn't block a poll loop forever.
_HTTP_TIMEOUT = 120

# Predictions download is a different beast — a single job can emit multiple GB.
# This is a *socket read* timeout (fail only if no bytes arrive for this long),
# not a total-transfer cap, so a legitimately slow multi-GB stream still
# completes. The original 120s read-all is exactly what TimeoutError'd in prod.
_PREDICTIONS_TIMEOUT = 1800

# Stream downloads in 1 MiB chunks so memory stays flat regardless of object size.
_DOWNLOAD_CHUNK = 1 << 20


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


def _gcs_upload_bytes(uri: str, data: bytes, mime: str, token: str) -> None:
    bucket, obj = parse_gcs_uri(uri)
    url = (
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
        f"?uploadType=media&name={urllib.parse.quote(obj, safe='')}"
    )
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": mime},
        method="POST",
    )
    _send(req, f"GCS upload {uri}")


def _gcs_download_to_file(uri: str, token: str, fileobj, *, timeout: int | None = _PREDICTIONS_TIMEOUT) -> None:
    """Stream a GCS object into `fileobj` in fixed chunks — flat memory at any size.

    Uses the predictions socket-read timeout (large) by default: multi-GB
    prediction files are legitimately slow, and the single read-all-into-RAM that
    this replaces is what TimeoutError'd in production on a 109 MB file.
    """
    bucket, obj = parse_gcs_uri(uri)
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
        f"{urllib.parse.quote(obj, safe='')}?alt=media"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted Google endpoint)
            shutil.copyfileobj(resp, fileobj, _DOWNLOAD_CHUNK)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        raise VertexBatchError(f"GCS download {uri} failed: HTTP {e.code} {detail}") from e


def _send(req: urllib.request.Request, what: str) -> str:
    return _send_bytes(req, what).decode()


def _send_bytes(req: urllib.request.Request, what: str) -> bytes:
    """Control-plane GET/POST (small bodies) — uses the short _HTTP_TIMEOUT."""
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
        timeout=_HTTP_TIMEOUT,  # control-plane: small body, short timeout
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


# Auth-token lifetime guard. ADC/gcloud access tokens live ~1h; a long batch
# (queue + run can exceed that) outlives a single token, after which control-plane
# and GCS calls 401 with UNAUTHENTICATED / ACCESS_TOKEN_TYPE_UNSUPPORTED. The fix
# is twofold: mint a FRESH token before each long-phase call (prevention), and, if
# one still 401s, re-mint and retry once (resilience) instead of abandoning a job
# that is still running — and succeeding — server-side.
_AUTH_ERROR_MARKERS = ("HTTP 401", "UNAUTHENTICATED", "ACCESS_TOKEN_TYPE_UNSUPPORTED")


def _is_auth_error(e: Exception) -> bool:
    msg = str(e)
    return any(m in msg for m in _AUTH_ERROR_MARKERS)


def _with_fresh_token(token_fn, call, *, on_event=None):
    """Run `call(token)` with a freshly minted token; on a single auth 401, re-mint
    and retry once. Non-auth errors propagate unchanged."""
    try:
        return call(token_fn())
    except VertexBatchError as e:
        if not _is_auth_error(e):
            raise
        if on_event:
            on_event("reauth", str(e)[:120])
        return call(token_fn())  # token expired mid-run — mint a fresh one and retry


# --------------------------------------------------------------------------- request building
def request_key(request: dict) -> str:
    """Full-body hash of a request — exact, but brittle as an output-correlation key.

    Kept for completeness/tests. NOT used to map predictions back to rows: Vertex
    Batch echoes the request with added/normalized fields (e.g. a default
    `candidateCount`), so a full-body hash of the echo won't match the input hash.
    Use `request_signature` for correlation.
    """
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


def request_signature(request: dict) -> str:
    """Stable identity of a request, robust to Vertex's echo normalization.

    Correlating a prediction back to its row by *position* is unsafe — Vertex Batch
    does **not** guarantee output order == input order, so positional mapping
    silently scrambles which image lands at which `out` path (and mislabels which
    row was safety-filtered). A full-body hash is also unsafe: the echoed request
    carries extra/normalized fields the input never had, so it won't match.

    This keys off only the fields Vertex passes through verbatim — the prompt text
    and the ordered ref `fileUri`s — which both the input request and its output
    echo share. Two rows that are genuinely identical (same prompt + refs) collapse
    to one signature and are filled FIFO; their outputs are interchangeable anyway.
    """
    contents = request.get("contents") or [{}]
    parts = contents[0].get("parts", []) if contents else []
    text = ""
    refs: list[str] = []
    for p in parts:
        if not text and isinstance(p.get("text"), str):
            text = p["text"]
        fd = p.get("fileData") or p.get("file_data")
        if isinstance(fd, dict):
            refs.append(fd.get("fileUri") or fd.get("file_uri") or "")
    payload = json.dumps({"text": text, "refs": refs}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class VertexBatchJob:
    model_id: str
    location: str
    rows: list  # list[BatchRow]
    input_uri: str
    output_prefix: str
    request_lines: list[dict] = field(default_factory=list)
    # request_signature → FIFO queue of out paths (handles duplicate identical requests)
    key_to_outs: dict = field(default_factory=dict)
    # gcsUri → local ref path to upload once before submit (deduped). Empty for
    # text-only jobs. Refs are referenced by fileData/gcsUri, NOT inlined as
    # base64 — so Vertex's per-line request echo stays tiny (the 7GB→<2GB fix).
    ref_uploads: dict = field(default_factory=dict)


def _ref_gcs_uri(prefix: str, model_safe: str, ref_path: str) -> str:
    """Deterministic GCS URI for a ref (path hash) — known at plan time, no upload.

    Naming by absolute-path hash keeps planning (and --dry-run) network- and
    disk-free while staying stable across runs, and dedupes when the same ref is
    reused by many rows.
    """
    if ref_path.startswith("gs://"):
        return ref_path  # already in GCS — reference directly, nothing to upload
    digest = hashlib.sha256(str(Path(ref_path).resolve()).encode()).hexdigest()[:16]
    return f"{prefix}/{model_safe}/refs/{digest}.png"


def build_request_line(row, force_size: str = BATCH_IMAGE_SIZE, ref_uris=()) -> dict:
    """Build one GenAI-batch input line: {"request": <generateContent body>}.

    `size` is forced to 1K (batch image output is 1K-only). Refs are passed as
    precomputed `ref_uris` (list of gcsUri) and referenced via `fileData` rather
    than inlined as base64, so the request — and Vertex's per-line echo of it in
    the output — stays tiny.
    """
    parts: list[dict] = [{"text": row.prompt}]
    for uri in ref_uris:
        parts.append({"fileData": {"mimeType": "image/png", "fileUri": uri}})
    gen_cfg: dict = {"responseModalities": ["IMAGE"]}
    img_cfg: dict = {}
    if row.aspect:
        img_cfg["aspectRatio"] = row.aspect
    if force_size:
        img_cfg["imageSize"] = force_size
    if img_cfg:
        gen_cfg["imageConfig"] = img_cfg
    return {"request": {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}}


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
            ref_uris = []
            for ref in row.refs or []:
                uri = _ref_gcs_uri(prefix, safe, str(ref))
                ref_uris.append(uri)
                if not str(ref).startswith("gs://"):
                    job.ref_uploads[uri] = str(ref)  # dedup: one upload per unique URI
            line = build_request_line(row, ref_uris=ref_uris)
            job.request_lines.append(line)
            job.key_to_outs.setdefault(request_signature(line["request"]), []).append(row.out)
        jobs.append(job)
    return jobs


def upload_refs(job: VertexBatchJob, token: str) -> None:
    """Upload each unique ref once (PNG, max edge 2048) to its deterministic URI.

    Done before submit so the request bodies' fileData URIs resolve. Idempotent
    enough to re-run (overwrites the same object).
    """
    for uri, ref_path in job.ref_uploads.items():
        b64, _ = encode_image_b64(ref_path, max_edge=2048, fmt="PNG")
        _gcs_upload_bytes(uri, base64.b64decode(b64), "image/png", token)


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


def _no_image_reason(resp: dict) -> str | None:
    """If a response carries no image, return a human reason (e.g. IMAGE_SAFETY).

    Surfaces the *real* cause — a safety/recitation block or empty candidate —
    instead of a generic "no image part" dump, so the final summary can say
    "row X failed: IMAGE_SAFETY". Returns None when an image part is present
    (the normal path) so the caller proceeds to decode + write.
    """
    pf = resp.get("promptFeedback") or resp.get("prompt_feedback") or {}
    block = pf.get("blockReason") or pf.get("block_reason")
    if block:
        return f"prompt blocked: {block} (no image returned)"
    for cand in resp.get("candidates", []):
        parts = cand.get("content", {}).get("parts", []) or []
        has_img = any((p.get("inlineData") or p.get("inline_data")) for p in parts)
        if has_img:
            return None
        reason = cand.get("finishReason") or cand.get("finish_reason")
        if reason and reason not in ("STOP", "MAX_TOKENS"):
            return f"{reason} (no image returned)"
    return None


class _OutputSink:
    """Streaming consumer: decode each output line's image straight to its `out`.

    Single-pass and stateful, so prediction shards can be read line-by-line off a
    temp file and discarded — peak memory is one line + one image, regardless of
    the (multi-GB) total output size.

    Correlation strategy is decided once from the first line and held for the
    whole stream:
      - **signature** when Vertex echoes the request (it does): map each
        prediction to its row by `request_signature` (prompt text + ref URIs).
        This is robust to Vertex Batch returning predictions OUT OF INPUT ORDER —
        the production bug that positional mapping caused (every image written to
        the wrong `out` path, identities cross-contaminated). In this mode a line
        whose signature matches nothing is reported as an error rather than
        silently misplaced.
      - **positional** ONLY when no request is echoed at all — a genuine last
        resort. Vertex Batch does not guarantee order, so this is best-effort;
        shards are read in sorted order to at least keep the running index stable.
    """

    def __init__(self, job: VertexBatchJob):
        self.job = job
        self.ordered = [r.out for r in job.rows]
        self.pending = {k: list(v) for k, v in job.key_to_outs.items()}
        self.mode: str | None = None
        self.idx = 0  # global output-line index across all shards
        self.placed = 0  # output lines that mapped to some row
        self.written = 0
        self.errors: list[str] = []

    def _target(self, line: dict):
        req = line.get("request")
        if self.mode is None:
            # Echoed request → signature mode (order-independent). No request on the
            # first line → positional fallback for the whole stream.
            self.mode = "signature" if isinstance(req, dict) else "positional"
        if self.mode == "signature":
            outs = self.pending.get(request_signature(req)) if isinstance(req, dict) else None
            return outs.pop(0) if outs else None
        return self.ordered[self.idx] if self.idx < len(self.ordered) else None

    def consume(self, line: dict) -> None:
        target = self._target(line)
        self.idx += 1
        if target is not None:
            self.placed += 1
        resp = _line_response(line)
        if resp is None:
            self.errors.append(f"{target or '?'}: no response/prediction ({json.dumps(line)[:200]})")
            return
        if target is None:
            # Signature mode with no match: do NOT guess a position — that's the
            # silent-corruption path. Report it so the row shows up as missing.
            self.errors.append(f"output line matched no input row (signature mismatch): {json.dumps(line)[:200]}")
            return
        reason = _no_image_reason(resp)
        if reason is not None:  # safety-filtered / empty — report with the CORRECT row
            self.errors.append(f"{target}: {reason}")
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gemini_extract(resp))
            self.written += 1
        except Exception as e:  # one bad line must not sink the fetch
            self.errors.append(f"{target}: {e}")

    def result(self) -> tuple[int, list[str]]:
        errors = list(self.errors)
        missing = len(self.job.rows) - self.placed
        if missing > 0:
            errors.append(f"{missing} row(s) had no output line returned")
        return self.written, errors


def _map_outputs(job: VertexBatchJob, output_lines: list[dict]) -> tuple[int, list[str]]:
    """List-based convenience wrapper over the streaming sink (used by tests)."""
    sink = _OutputSink(job)
    for line in output_lines:
        sink.consume(line)
    return sink.result()


# Final prediction outputs: `predictions.jsonl` or sharded `predictions.jsonl-00000-of-00002`.
# Anchored so we do NOT also grab `incremental_predictions.jsonl` (a partial dump
# Vertex may write *during* the run) — it sorts before the real shards and, in
# positional mode, would shift the global index and silently misroute every image.
_SHARD_RE = re.compile(r"^predictions\.jsonl(-\d+-of-\d+)?$")


def _is_prediction_shard(uri: str) -> bool:
    name = uri.rsplit("/", 1)[-1]
    return bool(_SHARD_RE.match(name))


def _stream_predictions(job: VertexBatchJob, token_fn, sink: _OutputSink, *, on_event=None) -> None:
    """Stream EVERY output shard through the sink, never holding a file in RAM.

    Each shard is downloaded chunk-by-chunk to a temp file, then iterated
    line-by-line; each line's image is decoded to disk and discarded. Shards are
    processed in sorted name order so positional correlation stays in input order.

    A fresh token is minted per GCS call (list + each shard download), with a single
    re-auth retry, so a download after a long poll can't 401 on an expired token.
    """
    shards = sorted(
        u for u in _with_fresh_token(token_fn, lambda t: _gcs_list(job.output_prefix, t), on_event=on_event)
        if _is_prediction_shard(u)
    )
    if not shards:
        sink.errors.append(f"no prediction shards under {job.output_prefix}")
        return
    for shard in shards:
        with tempfile.NamedTemporaryFile(suffix=".jsonl") as tmp:
            _with_fresh_token(token_fn, lambda t: _gcs_download_to_file(shard, t, tmp), on_event=on_event)
            tmp.flush()
            tmp.seek(0)
            for raw in tmp:  # file iteration = one line in memory at a time
                raw = raw.strip()
                if raw:
                    sink.consume(json.loads(raw))


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

    written_total = 0
    all_errors: list[str] = []
    submitted: list[dict] = []  # job ids surfaced for manual resume of a finished job
    for job in jobs:
        # Mint per job: an earlier job's long poll may have aged the token past the
        # quick upload/submit calls below.
        token = token_fn()
        on_event("upload", job)
        upload_refs(job, token)  # refs once, by gcsUri (keeps requests + echo tiny)
        _gcs_upload_text(job.input_uri, "\n".join(json.dumps(ln) for ln in job.request_lines), token)
        on_event("submit", job)
        job_name = _submit_job(job.model_id, job.location, job.input_uri, job.output_prefix, token)
        # Surface the job id + output dir BEFORE the long poll, so a killed/expired
        # run can be recovered against the already-running (and billed) job.
        submitted.append({"model": job.model_id, "location": job.location,
                          "job_name": job_name, "output_prefix": job.output_prefix})
        on_event("submitted", submitted[-1])
        state = _poll(job_name, job.location, token_fn, poll_interval, poll_max_tries, on_event, _sleep)
        if state != _DONE:
            all_errors.append(f"{job.model_id}: job {job_name} ended {state}")
            continue
        on_event("fetch", job)
        sink = _OutputSink(job)
        _stream_predictions(job, token_fn, sink, on_event=on_event)  # fresh token per shard
        written, errors = sink.result()
        written_total += written
        all_errors.extend(errors)

    summary["written"] = written_total
    summary["errors"] = all_errors
    summary["submitted"] = submitted
    return summary


def _poll(job_name, location, token_fn, interval, max_tries, on_event, _sleep) -> str:
    """Poll a batch job to a terminal state, re-authing each iteration.

    Mints a fresh token for every GetBatchPredictionJob (with a single 401 retry),
    so a poll loop spanning longer than the ~1h token lifetime keeps working instead
    of crashing on UNAUTHENTICATED and losing a job that is still running.
    """
    for _ in range(max_tries):
        job = _with_fresh_token(
            token_fn, lambda t: _get_job(job_name, location, t), on_event=on_event
        )
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
