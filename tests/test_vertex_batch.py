"""Tests for nazca.vertex_batch — async Vertex Batch inference (item 2D).

Live HTTP/GCS/job calls are behind injectable seams; these exercise the pure
logic: request building (1K-forced), GCS URI parsing, model grouping + skip,
output→row correlation (incl. duplicate requests), and the dry-run plan.
"""

from __future__ import annotations

import base64
import json

import pytest

from nazca import vertex_batch as vb
from nazca.batch import BatchRow


def _row(tmp_path, name, model="nano-banana-pro", size=None, refs=None):
    return BatchRow(out=tmp_path / name, prompt=f"prompt for {name}", refs=refs or [], model=model, size=size)


# --------------------------------------------------------------------------- eligibility constant
def test_global_image_models_are_listed_eligible():
    # The 2D probe finding: the global-only image models support batch.
    assert "gemini-3-pro-image" in vb.BATCH_IMAGE_MODELS
    assert "gemini-3.1-flash-image" in vb.BATCH_IMAGE_MODELS


# --------------------------------------------------------------------------- gcs uri
def test_parse_gcs_uri():
    assert vb.parse_gcs_uri("gs://bucket/a/b.jsonl") == ("bucket", "a/b.jsonl")
    assert vb.parse_gcs_uri("gs://bucket") == ("bucket", "")


def test_parse_gcs_uri_rejects_non_gs():
    with pytest.raises(vb.VertexBatchError):
        vb.parse_gcs_uri("s3://bucket/x")
    with pytest.raises(vb.VertexBatchError):
        vb.parse_gcs_uri("gs:///nobucket")


# --------------------------------------------------------------------------- request building
def test_build_request_line_forces_1k(tmp_path):
    line = vb.build_request_line(_row(tmp_path, "a.png", size="4K"))
    cfg = line["request"]["generationConfig"]
    assert cfg["imageConfig"]["imageSize"] == "1K"  # batch is 1K-only
    assert cfg["responseModalities"] == ["IMAGE"]


def test_build_request_line_uses_filedata_not_base64(tmp_path):
    # Payload-shrink (E): refs are referenced by fileData/gcsUri, never inlined.
    line = vb.build_request_line(
        _row(tmp_path, "a.png", refs=["x"]),
        ref_uris=["gs://bkt/run/m/refs/abc123.png"],
    )
    parts = line["request"]["contents"][0]["parts"]
    assert parts[0] == {"text": "prompt for a.png"}
    assert parts[1] == {"fileData": {"mimeType": "image/png", "fileUri": "gs://bkt/run/m/refs/abc123.png"}}
    # no base64 anywhere in the serialized request
    assert "inlineData" not in json.dumps(line)


def test_plan_computes_ref_uris_and_uploads(tmp_path):
    r1 = BatchRow(out=tmp_path / "a.png", prompt="p", model="nano-banana-pro", refs=["/tmp/r1.png"])
    r2 = BatchRow(out=tmp_path / "b.png", prompt="p", model="nano-banana-pro", refs=["/tmp/r1.png"])  # same ref
    r3 = BatchRow(out=tmp_path / "c.png", prompt="p", model="nano-banana-pro", refs=["/tmp/r2.png"])
    job = vb.plan_vertex_jobs([r1, r2, r3], "gs://bkt/run")[0]
    # shared ref deduped → 2 unique uploads
    assert len(job.ref_uploads) == 2
    # request body carries fileData URIs, no base64
    assert "inlineData" not in json.dumps(job.request_lines)
    for line in job.request_lines:
        fds = [p for p in line["request"]["contents"][0]["parts"] if "fileData" in p]
        assert len(fds) == 1 and fds[0]["fileData"]["fileUri"].startswith("gs://bkt/run/")


def test_plan_passes_through_existing_gcs_refs(tmp_path):
    row = BatchRow(out=tmp_path / "a.png", prompt="p", model="nano-banana-pro", refs=["gs://other/ref.png"])
    job = vb.plan_vertex_jobs([row], "gs://bkt/run")[0]
    # already in GCS → referenced directly, nothing to upload
    assert job.ref_uploads == {}
    fd = job.request_lines[0]["request"]["contents"][0]["parts"][1]["fileData"]
    assert fd["fileUri"] == "gs://other/ref.png"


def test_warn_oversize_rows(tmp_path):
    rows = [_row(tmp_path, "a.png", size="2K"), _row(tmp_path, "b.png", size="1K"), _row(tmp_path, "c.png", size="4K")]
    over = vb.warn_oversize_rows(rows)
    assert {r.out.name for r in over} == {"a.png", "c.png"}


def test_request_key_is_stable_and_distinguishes(tmp_path):
    a = vb.build_request_line(_row(tmp_path, "a.png"))["request"]
    a2 = vb.build_request_line(_row(tmp_path, "a.png"))["request"]
    b = vb.build_request_line(_row(tmp_path, "a.png"))["request"]
    b["contents"][0]["parts"][0]["text"] = "different"
    assert vb.request_key(a) == vb.request_key(a2)
    assert vb.request_key(a) != vb.request_key(b)


# --------------------------------------------------------------------------- planning
def test_plan_groups_by_model_and_skips_existing(tmp_path):
    done = tmp_path / "done.png"
    done.write_bytes(b"x")
    rows = [
        _row(tmp_path, "a.png", model="nano-banana-pro"),
        _row(tmp_path, "b.png", model="nano-banana-pro"),
        _row(tmp_path, "c.png", model="nano-banana-2"),
        BatchRow(out=done, prompt="p", model="nano-banana-pro"),
    ]
    jobs = vb.plan_vertex_jobs(rows, "gs://bkt/run")
    by_model = {j.model_id: j for j in jobs}
    assert set(by_model) == {"gemini-3-pro-image", "gemini-3.1-flash-image"}
    assert len(by_model["gemini-3-pro-image"].rows) == 2  # done.png skipped
    # global models use the global location
    assert by_model["gemini-3-pro-image"].location == "global"
    # GCS layout
    assert by_model["gemini-3-pro-image"].input_uri == "gs://bkt/run/gemini-3-pro-image/input.jsonl"
    assert by_model["gemini-3-pro-image"].output_prefix == "gs://bkt/run/gemini-3-pro-image/output/"


def test_plan_rejects_non_gemini_models(tmp_path):
    rows = [_row(tmp_path, "a.png", model="seedream")]
    with pytest.raises(vb.VertexBatchError, match="only supports Vertex Gemini"):
        vb.plan_vertex_jobs(rows, "gs://bkt/run")


def test_plan_rejects_non_gcs_prefix(tmp_path):
    rows = [_row(tmp_path, "a.png")]
    with pytest.raises(vb.VertexBatchError, match="gs://"):
        vb.plan_vertex_jobs(rows, "/local/path")


# --------------------------------------------------------------------------- output mapping
def _resp_with_image(b64="aGVsbG8="):  # "hello"
    return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": b64}}]}}]}


def test_map_outputs_writes_images_by_request_key(tmp_path):
    rows = [_row(tmp_path, "a.png"), _row(tmp_path, "b.png")]
    job = vb.plan_vertex_jobs(rows, "gs://bkt/run")[0]
    # craft output lines echoing each request with a distinct image
    out_lines = [
        {"request": line["request"], "response": _resp_with_image(base64.b64encode(f"img-{i}".encode()).decode())}
        for i, line in enumerate(job.request_lines)
    ]
    written, errors = vb._map_outputs(job, out_lines)
    assert written == 2
    assert errors == []
    assert (tmp_path / "a.png").exists() and (tmp_path / "b.png").exists()


def test_map_outputs_duplicate_requests_fifo(tmp_path):
    # Two rows with an identical request (same prompt/model/no refs) → same key.
    r1 = BatchRow(out=tmp_path / "x.png", prompt="same", model="nano-banana-pro")
    r2 = BatchRow(out=tmp_path / "y.png", prompt="same", model="nano-banana-pro")
    job = vb.plan_vertex_jobs([r1, r2], "gs://bkt/run")[0]
    assert len(job.key_to_outs) == 1  # collapsed to one key
    out_lines = [
        {"request": job.request_lines[0]["request"], "response": _resp_with_image()},
        {"request": job.request_lines[0]["request"], "response": _resp_with_image()},
    ]
    written, errors = vb._map_outputs(job, out_lines)
    assert written == 2  # both outs filled via FIFO
    assert (tmp_path / "x.png").exists() and (tmp_path / "y.png").exists()


def test_map_outputs_missing_response_is_error(tmp_path):
    job = vb.plan_vertex_jobs([_row(tmp_path, "a.png")], "gs://bkt/run")[0]
    out_lines = [{"request": job.request_lines[0]["request"], "status": {"code": 3, "message": "bad"}}]
    written, errors = vb._map_outputs(job, out_lines)
    assert written == 0
    assert any("no response/prediction" in e for e in errors)


def test_map_outputs_positional_when_request_not_echoed(tmp_path):
    # Vertex may not echo our exact request JSON — fall back to input order.
    rows = [_row(tmp_path, "a.png"), _row(tmp_path, "b.png")]
    job = vb.plan_vertex_jobs(rows, "gs://bkt/run")[0]
    out_lines = [
        {"response": _resp_with_image(base64.b64encode(b"first").decode())},   # no `request`
        {"response": _resp_with_image(base64.b64encode(b"second").decode())},
    ]
    written, errors = vb._map_outputs(job, out_lines)
    assert written == 2 and errors == []
    assert (tmp_path / "a.png").read_bytes() == b"first"
    assert (tmp_path / "b.png").read_bytes() == b"second"


def test_map_outputs_accepts_prediction_field(tmp_path):
    # classic predictionsFormat uses `prediction` rather than `response`
    job = vb.plan_vertex_jobs([_row(tmp_path, "a.png")], "gs://bkt/run")[0]
    out_lines = [{"prediction": _resp_with_image()}]
    written, errors = vb._map_outputs(job, out_lines)
    assert written == 1 and errors == []


def test_map_outputs_reports_rows_with_no_output(tmp_path):
    rows = [_row(tmp_path, "a.png"), _row(tmp_path, "b.png")]
    job = vb.plan_vertex_jobs(rows, "gs://bkt/run")[0]
    out_lines = [{"response": _resp_with_image()}]  # only 1 of 2 rows returned
    written, errors = vb._map_outputs(job, out_lines)
    assert written == 1
    assert any("no output line returned" in e for e in errors)


def _normalized_echo(request_line):
    """Mimic Vertex echoing our request back with an added/normalized field — which
    breaks a full-body hash but must NOT break signature correlation."""
    req = json.loads(json.dumps(request_line["request"]))
    req["generationConfig"]["candidateCount"] = 1  # field we never sent
    return req


def test_map_outputs_signature_survives_reordering_and_echo_normalization(tmp_path):
    # THE production bug: Vertex Batch returns predictions OUT OF INPUT ORDER and
    # echoes a normalized request. Positional/full-hash mapping scrambled every
    # image across rows. Signature (prompt+refs) must place each image correctly.
    rows = [_row(tmp_path, "a.png"), _row(tmp_path, "b.png"), _row(tmp_path, "c.png")]
    job = vb.plan_vertex_jobs(rows, "gs://bkt/run")[0]
    # outputs served REVERSED, each carrying a normalized echo of its own request
    out_lines = [
        {"request": _normalized_echo(job.request_lines[2]),
         "response": _resp_with_image(base64.b64encode(b"C").decode())},
        {"request": _normalized_echo(job.request_lines[0]),
         "response": _resp_with_image(base64.b64encode(b"A").decode())},
        {"request": _normalized_echo(job.request_lines[1]),
         "response": _resp_with_image(base64.b64encode(b"B").decode())},
    ]
    written, errors = vb._map_outputs(job, out_lines)
    assert written == 3 and errors == []
    assert (tmp_path / "a.png").read_bytes() == b"A"  # not scrambled
    assert (tmp_path / "b.png").read_bytes() == b"B"
    assert (tmp_path / "c.png").read_bytes() == b"C"


def test_map_outputs_signature_distinguishes_rows_sharing_a_prompt_by_ref(tmp_path):
    # Same prompt, different ref → distinct signatures → no cross-contamination.
    r1 = BatchRow(out=tmp_path / "x.png", prompt="hero", model="nano-banana-pro", refs=["gs://b/r1.png"])
    r2 = BatchRow(out=tmp_path / "y.png", prompt="hero", model="nano-banana-pro", refs=["gs://b/r2.png"])
    job = vb.plan_vertex_jobs([r1, r2], "gs://bkt/run")[0]
    assert len(job.key_to_outs) == 2  # refs disambiguate identical prompts
    out_lines = [  # reversed order
        {"request": _normalized_echo(job.request_lines[1]),
         "response": _resp_with_image(base64.b64encode(b"Y").decode())},
        {"request": _normalized_echo(job.request_lines[0]),
         "response": _resp_with_image(base64.b64encode(b"X").decode())},
    ]
    written, errors = vb._map_outputs(job, out_lines)
    assert written == 2 and errors == []
    assert (tmp_path / "x.png").read_bytes() == b"X"
    assert (tmp_path / "y.png").read_bytes() == b"Y"


def test_map_outputs_reports_image_safety_with_correct_row(tmp_path):
    # A safety-filtered row must be reported by its CORRECT identity, even when the
    # filtered prediction is returned out of order.
    rows = [_row(tmp_path, "a.png"), _row(tmp_path, "b.png")]
    job = vb.plan_vertex_jobs(rows, "gs://bkt/run")[0]
    safety = {"candidates": [{"finishReason": "IMAGE_SAFETY", "content": {"parts": []}}]}
    out_lines = [  # b is safety-filtered, returned first (out of order)
        {"request": _normalized_echo(job.request_lines[1]), "response": safety},
        {"request": _normalized_echo(job.request_lines[0]), "response": _resp_with_image()},
    ]
    written, errors = vb._map_outputs(job, out_lines)
    assert written == 1
    assert (tmp_path / "a.png").exists()
    assert not (tmp_path / "b.png").exists()  # filtered → not written
    assert any("b.png" in e and "IMAGE_SAFETY" in e for e in errors)
    assert not any("a.png" in e for e in errors)  # a is fine — not mislabeled


def test_map_outputs_signature_mismatch_is_error_not_silent_misplace(tmp_path):
    # An output line whose signature matches nothing must surface as an error, not
    # get silently dropped into a positional slot.
    rows = [_row(tmp_path, "a.png")]
    job = vb.plan_vertex_jobs(rows, "gs://bkt/run")[0]
    bogus = {"contents": [{"role": "user", "parts": [{"text": "never requested"}]}],
             "generationConfig": {}}
    out_lines = [{"request": bogus, "response": _resp_with_image()}]
    written, errors = vb._map_outputs(job, out_lines)
    assert written == 0
    assert not (tmp_path / "a.png").exists()
    assert any("signature mismatch" in e for e in errors)


# --------------------------------------------------------------------------- orchestrator
def test_run_vertex_batch_dry_run_no_io(tmp_path, monkeypatch):
    # Any live call would blow up — assert none happens in dry-run.
    for fn in ("_gcs_upload_text", "_submit_job", "_get_job"):
        monkeypatch.setattr(vb, fn, lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"{fn} called")))
    rows = [_row(tmp_path, "a.png", size="2K"), _row(tmp_path, "b.png", model="nano-banana-2")]
    summary = vb.run_vertex_batch(rows, "gs://bkt/run", dry_run=True)
    assert summary["jobs"] == 2
    assert summary["pending"] == 2
    assert summary["oversize_forced_1k"] == 1
    assert "planned" in summary
    assert summary["planned"][0]["sample_request"] is not None


def _install_fake_transport(monkeypatch, rows, shard_payloads, *, states=("JOB_STATE_SUCCEEDED",)):
    """Wire up injectable seams: capture uploads, fake submit/poll, and serve
    prediction shards by streaming bytes into the caller's temp file object.

    `shard_payloads` is a list of lists-of-lines (one inner list per shard).
    """
    captured = {"uploaded_text": {}, "uploaded_bytes": {}}

    monkeypatch.setattr(vb, "_gcs_upload_text", lambda uri, text, token: captured["uploaded_text"].__setitem__(uri, text))
    monkeypatch.setattr(vb, "_gcs_upload_bytes", lambda uri, data, mime, token: captured["uploaded_bytes"].__setitem__(uri, data))
    monkeypatch.setattr(vb, "_submit_job", lambda *a, **k: "projects/p/locations/global/batchPredictionJobs/1")
    st = iter(states)
    monkeypatch.setattr(vb, "_get_job", lambda *a, **k: {"state": next(st, "JOB_STATE_SUCCEEDED")})

    shard_uris = [f"gs://bkt/run/gemini-3-pro-image/output/predictions.jsonl-{i:05d}-of-{len(shard_payloads):05d}"
                  for i in range(len(shard_payloads))]
    by_uri = {uri: lines for uri, lines in zip(shard_uris, shard_payloads)}

    # list returns shards out of order, to prove _stream_predictions sorts them
    monkeypatch.setattr(vb, "_gcs_list", lambda prefix, token: list(reversed(shard_uris)))

    def fake_download(uri, token, fileobj, **kw):
        body = "\n".join(json.dumps(ln) for ln in by_uri[uri]).encode()
        fileobj.write(body)  # simulate streaming bytes into the temp file

    monkeypatch.setattr(vb, "_gcs_download_to_file", fake_download)
    return captured


def test_run_vertex_batch_happy_path_streaming(tmp_path, monkeypatch):
    rows = [_row(tmp_path, "a.png"), _row(tmp_path, "b.png")]
    job = vb.plan_vertex_jobs(rows, "gs://bkt/run")[0]
    # one shard, positional order, each row a distinct image
    shard = [
        {"response": _resp_with_image(base64.b64encode(b"img-a").decode())},
        {"response": _resp_with_image(base64.b64encode(b"img-b").decode())},
    ]
    captured = _install_fake_transport(monkeypatch, rows, [shard], states=("JOB_STATE_RUNNING", "JOB_STATE_SUCCEEDED"))

    summary = vb.run_vertex_batch(rows, "gs://bkt/run", token_fn=lambda: "tok", poll_interval=0, _sleep=lambda s: None)
    assert summary["written"] == 2 and summary["errors"] == []
    assert (tmp_path / "a.png").read_bytes() == b"img-a"
    assert (tmp_path / "b.png").read_bytes() == b"img-b"
    # input JSONL uploaded, one line per row
    (uri,) = captured["uploaded_text"]
    assert len(captured["uploaded_text"][uri].splitlines()) == 2
    _ = job  # plan is deterministic; job built above only for clarity


def test_is_prediction_shard_matching():
    # final outputs match; partial/metadata files must NOT (they'd misroute output)
    assert vb._is_prediction_shard("gs://b/out/predictions.jsonl")
    assert vb._is_prediction_shard("gs://b/out/predictions.jsonl-00000-of-00002")
    assert vb._is_prediction_shard("gs://b/out/predictions.jsonl-00001-of-00002")
    assert not vb._is_prediction_shard("gs://b/out/incremental_predictions.jsonl")
    assert not vb._is_prediction_shard("gs://b/out/predictions.jsonl.metadata")
    assert not vb._is_prediction_shard("gs://b/out/row_count.json")


def test_stream_skips_incremental_predictions(tmp_path, monkeypatch):
    # An incremental_predictions.jsonl present alongside the real shard must be
    # ignored — else positional correlation would shift and misroute every image.
    rows = [_row(tmp_path, "a.png"), _row(tmp_path, "b.png")]
    monkeypatch.setattr(vb, "_gcs_upload_text", lambda *a, **k: None)
    monkeypatch.setattr(vb, "_gcs_upload_bytes", lambda *a, **k: None)
    monkeypatch.setattr(vb, "_submit_job", lambda *a, **k: "jobs/1")
    monkeypatch.setattr(vb, "_get_job", lambda *a, **k: {"state": "JOB_STATE_SUCCEEDED"})

    real = "gs://bkt/run/gemini-3-pro-image/output/predictions.jsonl-00000-of-00001"
    incremental = "gs://bkt/run/gemini-3-pro-image/output/incremental_predictions.jsonl"
    monkeypatch.setattr(vb, "_gcs_list", lambda prefix, token: [incremental, real])

    def fake_download(uri, token, fileobj, **kw):
        if uri == incremental:
            raise AssertionError("incremental file must not be downloaded")
        lines = [
            {"response": _resp_with_image(base64.b64encode(b"A").decode())},
            {"response": _resp_with_image(base64.b64encode(b"B").decode())},
        ]
        fileobj.write("\n".join(json.dumps(ln) for ln in lines).encode())

    monkeypatch.setattr(vb, "_gcs_download_to_file", fake_download)
    summary = vb.run_vertex_batch(rows, "gs://bkt/run", token_fn=lambda: "tok", poll_interval=0, _sleep=lambda s: None)
    assert summary["written"] == 2 and summary["errors"] == []
    assert (tmp_path / "a.png").read_bytes() == b"A"
    assert (tmp_path / "b.png").read_bytes() == b"B"


def test_stream_predictions_across_two_shards(tmp_path, monkeypatch):
    # THE scale test: 4 rows split across 2 shards, served out of order, streamed
    # line-by-line — each image must land at the right out path.
    rows = [_row(tmp_path, f"{c}.png") for c in "abcd"]
    shard0 = [
        {"response": _resp_with_image(base64.b64encode(b"A").decode())},
        {"response": _resp_with_image(base64.b64encode(b"B").decode())},
    ]
    shard1 = [
        {"response": _resp_with_image(base64.b64encode(b"C").decode())},
        {"response": _resp_with_image(base64.b64encode(b"D").decode())},
    ]
    _install_fake_transport(monkeypatch, rows, [shard0, shard1])

    summary = vb.run_vertex_batch(rows, "gs://bkt/run", token_fn=lambda: "tok", poll_interval=0, _sleep=lambda s: None)
    assert summary["written"] == 4 and summary["errors"] == []
    # positional correlation holds across sorted shards (shard0 then shard1)
    assert (tmp_path / "a.png").read_bytes() == b"A"
    assert (tmp_path / "b.png").read_bytes() == b"B"
    assert (tmp_path / "c.png").read_bytes() == b"C"
    assert (tmp_path / "d.png").read_bytes() == b"D"


def test_stream_predictions_is_single_pass_flat_memory(tmp_path, monkeypatch):
    # Memory stays flat: the sink consumes one line at a time and never receives
    # a whole-file blob. We assert download streams into a file object (not RAM)
    # and that consume() is called exactly once per line.
    rows = [_row(tmp_path, f"{i}.png") for i in range(5)]
    shard = [{"response": _resp_with_image(base64.b64encode(f"x{i}".encode()).decode())} for i in range(5)]
    _install_fake_transport(monkeypatch, rows, [shard])

    seen = {"lines": 0, "max_line_len": 0}
    orig_consume = vb._OutputSink.consume

    def counting_consume(self, line):
        seen["lines"] += 1
        seen["max_line_len"] = max(seen["max_line_len"], len(json.dumps(line)))
        return orig_consume(self, line)

    monkeypatch.setattr(vb._OutputSink, "consume", counting_consume)
    summary = vb.run_vertex_batch(rows, "gs://bkt/run", token_fn=lambda: "tok", poll_interval=0, _sleep=lambda s: None)
    assert summary["written"] == 5
    assert seen["lines"] == 5  # one consume per line, never the whole file at once


def test_run_vertex_batch_uploads_refs_once(tmp_path, monkeypatch):
    from PIL import Image
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    # two rows share the SAME ref → must upload it exactly once
    rows = [
        BatchRow(out=tmp_path / "a.png", prompt="p", model="nano-banana-pro", refs=[str(ref)]),
        BatchRow(out=tmp_path / "b.png", prompt="p2", model="nano-banana-pro", refs=[str(ref)]),
    ]
    shard = [
        {"response": _resp_with_image(base64.b64encode(b"A").decode())},
        {"response": _resp_with_image(base64.b64encode(b"B").decode())},
    ]
    captured = _install_fake_transport(monkeypatch, rows, [shard])

    summary = vb.run_vertex_batch(rows, "gs://bkt/run", token_fn=lambda: "tok", poll_interval=0, _sleep=lambda s: None)
    assert summary["written"] == 2
    assert len(captured["uploaded_bytes"]) == 1  # deduped: one upload for the shared ref


def test_dry_run_shows_filedata_refs_no_base64_no_io(tmp_path, monkeypatch):
    # --dry-run parity: the planned request matches what's sent (fileData gcsUri),
    # contains no base64, and makes zero network calls.
    for fn in ("_gcs_upload_text", "_gcs_upload_bytes", "_submit_job", "_get_job", "_gcs_list", "_gcs_download_to_file"):
        monkeypatch.setattr(vb, fn, lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"{fn} called in dry-run")))
    row = BatchRow(out=tmp_path / "a.png", prompt="p", model="nano-banana-pro", refs=["/tmp/r.png"])
    summary = vb.run_vertex_batch([row], "gs://bkt/run", dry_run=True)
    sample = summary["planned"][0]["sample_request"]
    blob = json.dumps(sample)
    assert "inlineData" not in blob and "base64" not in blob
    assert any("fileData" in p for p in sample["parts"])


def test_run_vertex_batch_failed_job_reported(tmp_path, monkeypatch):
    rows = [_row(tmp_path, "a.png")]
    monkeypatch.setattr(vb, "_gcs_upload_text", lambda *a, **k: None)
    monkeypatch.setattr(vb, "_submit_job", lambda *a, **k: "jobs/1")
    monkeypatch.setattr(vb, "_get_job", lambda *a, **k: {"state": "JOB_STATE_FAILED"})

    summary = vb.run_vertex_batch(
        rows, "gs://bkt/run", token_fn=lambda: "tok", poll_interval=0, _sleep=lambda s: None,
    )
    assert summary["written"] == 0
    assert any("JOB_STATE_FAILED" in e for e in summary["errors"])


# --------------------------------------------------------------------------- auth-token refresh (long jobs)
def test_is_auth_error_recognizes_401_and_unauthenticated():
    assert vb._is_auth_error(vb.VertexBatchError("get job jobs/1 failed: HTTP 401 UNAUTHENTICATED"))
    assert vb._is_auth_error(vb.VertexBatchError("... ACCESS_TOKEN_TYPE_UNSUPPORTED ..."))
    assert not vb._is_auth_error(vb.VertexBatchError("get job jobs/1 failed: HTTP 500 boom"))


def test_poll_remints_token_each_iteration(monkeypatch):
    # A long poll must mint a FRESH token per iteration (token lives ~1h; the job
    # can outlive it). Assert token_fn is called once per GetBatchPredictionJob.
    minted = {"n": 0}
    def token_fn():
        minted["n"] += 1
        return f"tok-{minted['n']}"
    seen_tokens = []
    monkeypatch.setattr(vb, "_get_job", lambda job, loc, tok: (seen_tokens.append(tok), {"state": "JOB_STATE_RUNNING" if len(seen_tokens) < 3 else "JOB_STATE_SUCCEEDED"})[1])
    state = vb._poll("jobs/1", "global", token_fn, 0, 10, lambda *a: None, lambda s: None)
    assert state == "JOB_STATE_SUCCEEDED"
    assert seen_tokens == ["tok-1", "tok-2", "tok-3"]  # fresh token every poll


def test_poll_recovers_from_single_401(monkeypatch):
    # The exact production failure: GetBatchPredictionJob 401s on an expired token
    # mid-poll. We must re-auth + retry, not abort a job that's still running.
    calls = {"n": 0}
    events = []
    def flaky_get(job, loc, tok):
        calls["n"] += 1
        if calls["n"] == 1:
            raise vb.VertexBatchError("get job jobs/1 failed: HTTP 401 UNAUTHENTICATED ACCESS_TOKEN_TYPE_UNSUPPORTED")
        return {"state": "JOB_STATE_SUCCEEDED"}
    monkeypatch.setattr(vb, "_get_job", flaky_get)
    state = vb._poll("jobs/1", "global", lambda: "tok", 0, 5, lambda s, d=None: events.append((s, d)), lambda s: None)
    assert state == "JOB_STATE_SUCCEEDED"
    assert calls["n"] == 2  # first 401 → re-mint → success
    assert any(s == "reauth" for s, _ in events)


def test_non_auth_error_in_poll_is_not_retried(monkeypatch):
    calls = {"n": 0}
    def boom(job, loc, tok):
        calls["n"] += 1
        raise vb.VertexBatchError("get job jobs/1 failed: HTTP 500 server error")
    monkeypatch.setattr(vb, "_get_job", boom)
    with pytest.raises(vb.VertexBatchError, match="HTTP 500"):
        vb._poll("jobs/1", "global", lambda: "tok", 0, 5, lambda *a: None, lambda s: None)
    assert calls["n"] == 1  # 500 is not an auth error → no retry


def test_run_vertex_batch_surfaces_submitted_job_id(tmp_path, monkeypatch):
    # The job id + output dir must be reported (for manual resume of a billed job).
    rows = [_row(tmp_path, "a.png")]
    shard = [{"response": _resp_with_image(base64.b64encode(b"A").decode())}]
    _install_fake_transport(monkeypatch, rows, [shard])
    events = []
    summary = vb.run_vertex_batch(
        rows, "gs://bkt/run", token_fn=lambda: "tok", poll_interval=0, _sleep=lambda s: None,
        on_event=lambda stage, detail=None: events.append((stage, detail)),
    )
    assert summary["written"] == 1
    assert len(summary["submitted"]) == 1
    sub = summary["submitted"][0]
    assert sub["job_name"] == "projects/p/locations/global/batchPredictionJobs/1"
    assert sub["output_prefix"].endswith("/output/")
    assert any(stage == "submitted" for stage, _ in events)


def test_stream_predictions_recovers_from_401_on_download(tmp_path, monkeypatch):
    # A download after a long poll can hit an expired token — re-auth + retry once.
    rows = [_row(tmp_path, "a.png")]
    job = vb.plan_vertex_jobs(rows, "gs://bkt/run")[0]
    shard_uri = f"{job.output_prefix}predictions.jsonl-00000-of-00001"
    monkeypatch.setattr(vb, "_gcs_list", lambda prefix, tok: [shard_uri])
    calls = {"n": 0}
    def flaky_download(uri, tok, fileobj, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise vb.VertexBatchError(f"GCS download {uri} failed: HTTP 401 UNAUTHENTICATED")
        fileobj.write(json.dumps({"request": job.request_lines[0]["request"], "response": _resp_with_image(base64.b64encode(b"A").decode())}).encode())
    monkeypatch.setattr(vb, "_gcs_download_to_file", flaky_download)
    sink = vb._OutputSink(job)
    vb._stream_predictions(job, lambda: "tok", sink)
    written, errors = sink.result()
    assert written == 1 and errors == []
    assert calls["n"] == 2
    assert (tmp_path / "a.png").read_bytes() == b"A"
