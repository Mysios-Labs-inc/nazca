"""Tests for nazca.vertex_batch — async Vertex Batch inference (item 2D).

Live HTTP/GCS/job calls are behind injectable seams; these exercise the pure
logic: request building (1K-forced), GCS URI parsing, model grouping + skip,
output→row correlation (incl. duplicate requests), and the dry-run plan.
"""

from __future__ import annotations

import base64

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


def test_run_vertex_batch_happy_path(tmp_path, monkeypatch):
    rows = [_row(tmp_path, "a.png"), _row(tmp_path, "b.png")]

    uploaded = {}

    def fake_upload(uri, text, token):
        uploaded[uri] = text

    def fake_submit(model_id, location, input_uri, output_prefix, token):
        return f"projects/p/locations/{location}/batchPredictionJobs/123"

    states = iter(["JOB_STATE_RUNNING", "JOB_STATE_SUCCEEDED"])
    monkeypatch.setattr(vb, "_gcs_upload_text", fake_upload)
    monkeypatch.setattr(vb, "_submit_job", fake_submit)
    monkeypatch.setattr(vb, "_get_job", lambda *a, **k: {"state": next(states)})

    def fake_read_outputs(prefix, token):
        job = vb.plan_vertex_jobs(rows, "gs://bkt/run")[0]
        return [
            {"request": line["request"], "response": _resp_with_image(base64.b64encode(f"i{i}".encode()).decode())}
            for i, line in enumerate(job.request_lines)
        ]

    monkeypatch.setattr(vb, "read_output_lines", fake_read_outputs)

    summary = vb.run_vertex_batch(
        rows, "gs://bkt/run", token_fn=lambda: "tok", poll_interval=0, _sleep=lambda s: None,
    )
    assert summary["written"] == 2
    assert summary["errors"] == []
    assert (tmp_path / "a.png").exists() and (tmp_path / "b.png").exists()
    # input JSONL was uploaded with one line per row
    (uri,) = uploaded
    assert len(uploaded[uri].splitlines()) == 2


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
