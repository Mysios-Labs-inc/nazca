"""Tests for nazca.batch — paced, multi-lane, idempotent image batching (item 1B)."""

from __future__ import annotations

import json

import pytest

from nazca import batch


# --------------------------------------------------------------------------- pacing
def test_start_pacer_spaces_starts_not_post_gen():
    """A slow 'generation' between waits should NOT add to the interval."""
    clock = {"t": 0.0}
    slept: list[float] = []

    def fake_clock():
        return clock["t"]

    def fake_sleep(s):
        slept.append(s)
        clock["t"] += s

    pacer = batch._StartPacer(30.0, _clock=fake_clock, _sleep=fake_sleep)

    pacer.wait()  # first call: no wait
    assert slept == []

    clock["t"] += 22.0  # generation took 22s (< interval)
    pacer.wait()  # only needs to wait the remaining 8s
    assert slept == [8.0]

    clock["t"] += 45.0  # next gen took longer than the interval
    pacer.wait()  # no extra wait — already past the gate
    assert slept == [8.0]


def test_lane_interval_includes_margin():
    # 2/min → 30s + 1s margin so we sit under the ceiling, not on it.
    assert batch.lane_interval(2) == pytest.approx(31.0)
    assert batch.lane_interval(6) == pytest.approx(11.0)


def test_lane_interval_rejects_nonpositive():
    with pytest.raises(batch.BatchError):
        batch.lane_interval(0)
    with pytest.raises(batch.BatchError):
        batch.lane_interval(-1)


# --------------------------------------------------------------------------- manifest
def test_load_jsonl_manifest(tmp_path):
    mf = tmp_path / "jobs.jsonl"
    mf.write_text(
        json.dumps({"out": "a.png", "prompt": "cat", "model": "nano-banana"})
        + "\n"
        + json.dumps({"out": "b.png", "prompt": "dog", "ref": "r.png", "size": "4K"})
        + "\n"
    )
    rows = batch.load_manifest(mf)
    assert len(rows) == 2
    assert rows[0].prompt == "cat"
    assert rows[1].refs == ["r.png"]
    assert rows[1].size == "4K"


def test_load_csv_manifest_with_multi_refs(tmp_path):
    mf = tmp_path / "jobs.csv"
    mf.write_text("out,prompt,refs\nx.png,sunset,a.png;b.png\n")
    rows = batch.load_manifest(mf)
    assert rows[0].refs == ["a.png", "b.png"]


def test_load_csv_missing_required_column_raises(tmp_path):
    mf = tmp_path / "jobs.csv"
    mf.write_text("out,model\nx.png,nano-banana\n")  # no prompt column
    with pytest.raises(batch.BatchError, match="missing required"):
        batch.load_manifest(mf)


def test_manifest_defaults_fill_missing_fields(tmp_path):
    mf = tmp_path / "jobs.jsonl"
    mf.write_text(json.dumps({"out": "a.png", "prompt": "cat"}) + "\n")
    rows = batch.load_manifest(mf, defaults={"size": "2K", "model": "nano-banana-pro"})
    assert rows[0].size == "2K"
    assert rows[0].model == "nano-banana-pro"


def test_manifest_row_missing_required_raises(tmp_path):
    mf = tmp_path / "bad.jsonl"
    mf.write_text(json.dumps({"out": "a.png"}) + "\n")  # no prompt
    with pytest.raises(batch.BatchError, match="missing required"):
        batch.load_manifest(mf)


def test_empty_manifest_raises(tmp_path):
    mf = tmp_path / "empty.jsonl"
    mf.write_text("\n\n")
    with pytest.raises(batch.BatchError, match="empty"):
        batch.load_manifest(mf)


def test_unknown_manifest_suffix_raises(tmp_path):
    mf = tmp_path / "jobs.txt"
    mf.write_text("whatever")
    with pytest.raises(batch.BatchError, match="unsupported"):
        batch.load_manifest(mf)


# --------------------------------------------------------------------------- from-dir
def test_rows_from_dir_templates_prompt_and_fans_models(tmp_path):
    src = tmp_path / "refs"
    src.mkdir()
    (src / "one.png").write_bytes(b"x")
    (src / "two.jpg").write_bytes(b"y")
    out = tmp_path / "out"

    rows = batch.rows_from_dir(
        src, "logo for {stem}", out, models=["nano-banana", "seedream"],
    )
    # 2 files × 2 models = 4 rows
    assert len(rows) == 4
    prompts = {r.prompt for r in rows}
    assert "logo for one" in prompts and "logo for two" in prompts
    # multi-model → per-model subdir so outputs never collide
    outs = {str(r.out) for r in rows}
    assert any("nano-banana" in o for o in outs)
    assert any("seedream" in o for o in outs)


def test_rows_from_dir_requires_images(tmp_path):
    src = tmp_path / "empty"
    src.mkdir()
    with pytest.raises(batch.BatchError, match="no images"):
        batch.rows_from_dir(src, "p", tmp_path / "out")


# --------------------------------------------------------------------------- planning
def test_plan_groups_into_lanes_and_counts_pending(tmp_path):
    done = tmp_path / "done.png"
    done.write_bytes(b"x")  # already exists → skipped
    rows = [
        batch.BatchRow(out=tmp_path / "a.png", prompt="p", model="nano-banana"),
        batch.BatchRow(out=tmp_path / "b.png", prompt="p", model="nano-banana"),
        batch.BatchRow(out=done, prompt="p", model="seedream"),
    ]
    plan = batch.plan_batch(rows, rpm=2)
    assert set(plan.lanes) == {"nano-banana", "seedream"}
    assert plan.pending == 2
    assert plan.skipped == 1
    assert plan.total == 3


def test_plan_eta_uses_slowest_lane(tmp_path):
    rows = [batch.BatchRow(out=tmp_path / f"a{i}.png", prompt="p", model="nano-banana") for i in range(3)]
    rows += [batch.BatchRow(out=tmp_path / "b.png", prompt="p", model="seedream")]
    plan = batch.plan_batch(rows, rpm=2)
    # slowest lane = 3 pending → 2 gaps × 31s
    assert plan.eta_seconds() == pytest.approx(62.0)


def test_plan_only_models_filter(tmp_path):
    rows = [
        batch.BatchRow(out=tmp_path / "a.png", prompt="p", model="nano-banana"),
        batch.BatchRow(out=tmp_path / "b.png", prompt="p", model="seedream"),
    ]
    plan = batch.plan_batch(rows, rpm=2, only_models={"seedream"})
    assert set(plan.lanes) == {"seedream"}


def test_plan_uses_default_model_for_unspecified(tmp_path):
    rows = [batch.BatchRow(out=tmp_path / "a.png", prompt="p")]
    plan = batch.plan_batch(rows, rpm=2, default_model="nano-banana")
    assert set(plan.lanes) == {"nano-banana"}


def test_plan_raises_when_filter_empties(tmp_path):
    rows = [batch.BatchRow(out=tmp_path / "a.png", prompt="p", model="nano-banana")]
    with pytest.raises(batch.BatchError):
        batch.plan_batch(rows, rpm=2, only_models={"nonexistent"})


# --------------------------------------------------------------------------- execution
def test_run_batch_skips_existing_and_dispatches_pending(tmp_path, monkeypatch):
    done = tmp_path / "done.png"
    done.write_bytes(b"x")
    pending = tmp_path / "sub" / "new.png"

    calls: list[tuple] = []

    def fake_generate(out, prompt, *, ref, model, aspect_ratio, size, dry_run=False):
        calls.append((out, model, dry_run))
        out.write_bytes(b"img")
        return out

    monkeypatch.setattr(batch, "generate_image", fake_generate)

    rows = [
        batch.BatchRow(out=done, prompt="p", model="nano-banana"),
        batch.BatchRow(out=pending, prompt="p", model="nano-banana"),
    ]
    plan = batch.plan_batch(rows, rpm=600)  # tiny interval; real sleeps near-zero
    # zero-wait pacer so the test never actually sleeps
    results = batch.run_batch(
        plan, _pacer_factory=lambda interval: batch._StartPacer(0.0)
    )

    statuses = sorted(r.status for r in results)
    assert statuses == ["ok", "skipped"]
    assert len(calls) == 1  # only the pending row dispatched
    assert pending.exists()  # parent dir was created


def test_run_batch_isolates_row_errors(tmp_path, monkeypatch):
    def fake_generate(out, prompt, *, ref, model, aspect_ratio, size, dry_run=False):
        if "boom" in str(out):
            raise RuntimeError("provider exploded")
        out.write_bytes(b"img")
        return out

    monkeypatch.setattr(batch, "generate_image", fake_generate)

    rows = [
        batch.BatchRow(out=tmp_path / "boom.png", prompt="p", model="nano-banana"),
        batch.BatchRow(out=tmp_path / "fine.png", prompt="p", model="nano-banana"),
    ]
    plan = batch.plan_batch(rows, rpm=600)
    results = batch.run_batch(plan, _pacer_factory=lambda i: batch._StartPacer(0.0))

    by_status = {r.status for r in results}
    assert by_status == {"ok", "error"}
    err = next(r for r in results if r.status == "error")
    assert "exploded" in str(err.detail)


def test_run_batch_dry_run_plans_without_dispatch(tmp_path, monkeypatch):
    def fake_generate(out, prompt, *, ref, model, aspect_ratio, size, dry_run=False):
        assert dry_run is True  # dry-run must never really generate
        return {"url": "http://x", "model": model}

    monkeypatch.setattr(batch, "generate_image", fake_generate)

    rows = [batch.BatchRow(out=tmp_path / "a.png", prompt="p", model="nano-banana")]
    plan = batch.plan_batch(rows, rpm=2)
    results = batch.run_batch(plan, dry_run=True)
    assert results[0].status == "planned"
    assert results[0].detail["model"] == "nano-banana"


def test_run_batch_dry_run_isolates_plan_errors(tmp_path, monkeypatch):
    # A row whose plan can't even be built (e.g. bad ref) becomes an error,
    # not a crash that sinks the whole preview.
    def fake_generate(out, prompt, *, ref, model, aspect_ratio, size, dry_run=False):
        if model == "broken":
            raise RuntimeError("cannot build plan")
        return {"url": "http://x", "model": model}

    monkeypatch.setattr(batch, "generate_image", fake_generate)

    rows = [
        batch.BatchRow(out=tmp_path / "a.png", prompt="p", model="broken"),
        batch.BatchRow(out=tmp_path / "b.png", prompt="p", model="nano-banana"),
    ]
    plan = batch.plan_batch(rows, rpm=2)
    results = batch.run_batch(plan, dry_run=True)
    assert {r.status for r in results} == {"error", "planned"}


def test_run_batch_multi_lane_runs_in_parallel(tmp_path, monkeypatch):
    seen_models: list[str] = []

    def fake_generate(out, prompt, *, ref, model, aspect_ratio, size, dry_run=False):
        seen_models.append(model)
        out.write_bytes(b"img")
        return out

    monkeypatch.setattr(batch, "generate_image", fake_generate)

    rows = [
        batch.BatchRow(out=tmp_path / "a.png", prompt="p", model="nano-banana"),
        batch.BatchRow(out=tmp_path / "b.png", prompt="p", model="seedream"),
        batch.BatchRow(out=tmp_path / "c.png", prompt="p", model="nano-banana-pro"),
    ]
    plan = batch.plan_batch(rows, rpm=600)
    results = batch.run_batch(plan, _pacer_factory=lambda i: batch._StartPacer(0.0))
    assert len([r for r in results if r.status == "ok"]) == 3
    assert set(seen_models) == {"nano-banana", "seedream", "nano-banana-pro"}


def test_run_batch_respects_concurrency_cap(tmp_path, monkeypatch):
    def fake_generate(out, prompt, *, ref, model, aspect_ratio, size, dry_run=False):
        out.write_bytes(b"img")
        return out

    monkeypatch.setattr(batch, "generate_image", fake_generate)

    rows = [
        batch.BatchRow(out=tmp_path / f"{m}.png", prompt="p", model=m)
        for m in ("nano-banana", "seedream", "nano-banana-pro")
    ]
    plan = batch.plan_batch(rows, rpm=600)
    # Capping lanes to 1 must still complete all rows, just less parallel.
    results = batch.run_batch(
        plan, concurrency=1, _pacer_factory=lambda i: batch._StartPacer(0.0)
    )
    assert len([r for r in results if r.status == "ok"]) == 3


# ----------------------------------------------------- per-provider pacing + quality
def test_backend_of_keys_off_model():
    assert batch.backend_of("gpt-image-2") == "openai"
    assert batch.backend_of("nano-banana") == "vertex"


def test_quality_only_passed_when_set(tmp_path, monkeypatch):
    # Rows without a quality must keep the pre-quality call signature (no kwarg),
    # so legacy callers/fakes don't break. A row WITH quality threads it through.
    seen: list[dict] = []

    def fake_generate(out, prompt, *, ref, model, aspect_ratio, size, quality=None, dry_run=False):
        seen.append({"model": model, "quality": quality})
        out.write_bytes(b"img")
        return out

    monkeypatch.setattr(batch, "generate_image", fake_generate)

    rows = [
        batch.BatchRow(out=tmp_path / "plain.png", prompt="p", model="nano-banana"),
        batch.BatchRow(out=tmp_path / "hq.png", prompt="p", model="gpt-image-2", quality="medium"),
    ]
    # gpt-image-2 lane is concurrent; nano-banana lane is paced. Zero-wait pacer.
    plan = batch.plan_batch(rows, rpm=600)
    batch.run_batch(plan, _pacer_factory=lambda i: batch._StartPacer(0.0))

    by_model = {s["model"]: s["quality"] for s in seen}
    assert by_model["nano-banana"] is None
    assert by_model["gpt-image-2"] == "medium"


def test_quality_call_omits_kwarg_for_legacy_fakes(tmp_path, monkeypatch):
    # A fake WITHOUT a `quality` param must still work for non-quality rows —
    # proves the kwarg is omitted, not passed as None.
    def legacy_fake(out, prompt, *, ref, model, aspect_ratio, size, dry_run=False):
        out.write_bytes(b"img")
        return out

    monkeypatch.setattr(batch, "generate_image", legacy_fake)
    rows = [batch.BatchRow(out=tmp_path / "a.png", prompt="p", model="nano-banana")]
    plan = batch.plan_batch(rows, rpm=600)
    results = batch.run_batch(plan, _pacer_factory=lambda i: batch._StartPacer(0.0))
    assert results[0].status == "ok"


def test_openai_lane_runs_concurrently_without_pacing(tmp_path, monkeypatch):
    # gpt-image-2 is latency-bound: rows should dispatch in parallel (a worker
    # pool), so a blocking gen on one row overlaps the others. We assert the lane
    # reaches max in-flight > 1 — impossible with the single-threaded vertex pacer.
    import threading

    inflight = {"now": 0, "max": 0}
    lock = threading.Lock()
    release = threading.Event()

    def fake_generate(out, prompt, *, ref, model, aspect_ratio, size, quality=None, dry_run=False):
        with lock:
            inflight["now"] += 1
            inflight["max"] = max(inflight["max"], inflight["now"])
        release.wait(timeout=2.0)
        with lock:
            inflight["now"] -= 1
        out.write_bytes(b"img")
        return out

    monkeypatch.setattr(batch, "generate_image", fake_generate)
    rows = [batch.BatchRow(out=tmp_path / f"{i}.png", prompt="p", model="gpt-image-2") for i in range(3)]
    plan = batch.plan_batch(rows, rpm=2)  # rpm wall would serialize a vertex lane

    # Release the gate shortly after start so all three can pile in first.
    timer = threading.Timer(0.3, release.set)
    timer.start()
    results = batch.run_batch(plan, lane_workers=3)
    timer.cancel()

    assert len([r for r in results if r.status == "ok"]) == 3
    assert inflight["max"] > 1  # concurrency, not a throttle


def test_run_batch_isolates_mkdir_failure(tmp_path, monkeypatch):
    # A filesystem failure (parent path is a regular file, so mkdir fails) must
    # be captured as a per-row error, not escape the lane and sink the batch.
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"i am a file, not a dir")

    def fake_generate(out, prompt, *, ref, model, aspect_ratio, size, dry_run=False):
        out.write_bytes(b"img")
        return out

    monkeypatch.setattr(batch, "generate_image", fake_generate)

    rows = [
        batch.BatchRow(out=blocker / "nested" / "a.png", prompt="p", model="nano-banana"),
        batch.BatchRow(out=tmp_path / "ok.png", prompt="p", model="nano-banana"),
    ]
    plan = batch.plan_batch(rows, rpm=600)
    results = batch.run_batch(plan, _pacer_factory=lambda i: batch._StartPacer(0.0))
    assert {r.status for r in results} == {"error", "ok"}


# --------------------------------------------------------------------------- budget gate (CLI)
def _budget_dir(tmp_path):
    from PIL import Image
    d = tmp_path / "refs"
    d.mkdir()
    for n in ("a", "b", "c"):
        Image.new("RGB", (8, 8)).save(d / f"{n}.png")
    return d


def test_batch_max_cost_blocks_real_run_over_budget(tmp_path):
    from click.testing import CliRunner

    from nazca.cli import cli
    # 3 rows × nano-banana-pro @2K ($0.134) = $0.402 > $0.10 → refuse, exit 2, no dispatch
    r = CliRunner().invoke(cli, [
        "batch", "--from-dir", str(_budget_dir(tmp_path)), "--prompt", "x",
        "--models", "nano-banana-pro", "--size", "2K", "--out-dir", str(tmp_path / "out"),
        "--max-cost", "0.10",
    ])
    assert r.exit_code == 2, r.output
    assert "exceeds --max-cost $0.10" in r.output
    assert "nothing dispatched" in r.output


def test_batch_max_cost_within_budget_passes_gate(tmp_path):
    from click.testing import CliRunner

    from nazca.cli import cli
    # generous ceiling → gate passes; --dry-run avoids real dispatch
    r = CliRunner().invoke(cli, [
        "batch", "--from-dir", str(_budget_dir(tmp_path)), "--prompt", "x",
        "--models", "nano-banana-pro", "--size", "2K", "--out-dir", str(tmp_path / "out"),
        "--max-cost", "5.00", "--dry-run",
    ])
    assert r.exit_code == 0, r.output
    assert "within --max-cost $5.00" in r.output


def test_batch_max_cost_dry_run_over_budget_warns_but_succeeds(tmp_path):
    from click.testing import CliRunner

    from nazca.cli import cli
    r = CliRunner().invoke(cli, [
        "batch", "--from-dir", str(_budget_dir(tmp_path)), "--prompt", "x",
        "--models", "nano-banana-pro", "--size", "2K", "--out-dir", str(tmp_path / "out"),
        "--max-cost", "0.10", "--dry-run",
    ])
    assert r.exit_code == 0, r.output            # dry-run is a preview; it warns, doesn't fail
    assert "would exceed --max-cost $0.10" in r.output


# --------------------------------------------------------------------------- status / verify
def test_batch_status_splits_done_and_pending(tmp_path):
    done = tmp_path / "done.png"
    done.write_bytes(b"x")
    rows = [
        batch.BatchRow(out=done, prompt="p", model="nano-banana"),
        batch.BatchRow(out=tmp_path / "missing.png", prompt="p", model="nano-banana"),
    ]
    status = batch.batch_status(rows)
    assert status.total == 2
    assert [r.out for r in status.done] == [done]
    assert [r.out for r in status.pending] == [tmp_path / "missing.png"]


def test_batch_status_all_done_is_empty_pending(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    status = batch.batch_status([
        batch.BatchRow(out=a, prompt="p"),
        batch.BatchRow(out=b, prompt="p"),
    ])
    assert status.pending == []
    assert "2 done · 0 pending" in status.summary_lines()[0]


def _status_manifest(tmp_path, done_names, all_names):
    """Write a JSONL manifest and create the `out` files for done_names only."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    lines = []
    for n in all_names:
        out_path = out_dir / f"{n}.png"
        lines.append(json.dumps({"out": str(out_path), "prompt": "x", "model": "nano-banana"}))
        if n in done_names:
            out_path.write_bytes(b"x")
    manifest = tmp_path / "jobs.jsonl"
    manifest.write_text("\n".join(lines))
    return manifest


def test_batch_status_cli_exits_1_when_pending(tmp_path):
    from click.testing import CliRunner

    from nazca.cli import cli
    manifest = _status_manifest(tmp_path, done_names={"a"}, all_names=["a", "b", "c"])
    r = CliRunner().invoke(cli, ["batch", str(manifest), "--status"])
    assert r.exit_code == 1, r.output
    assert "3 rows · 1 done · 2 pending" in r.output
    assert "b.png" in r.output and "c.png" in r.output


def test_batch_status_cli_exits_0_when_complete(tmp_path):
    from click.testing import CliRunner

    from nazca.cli import cli
    manifest = _status_manifest(tmp_path, done_names={"a", "b"}, all_names=["a", "b"])
    r = CliRunner().invoke(cli, ["batch", str(manifest), "--status"])
    assert r.exit_code == 0, r.output
    assert "2 done · 0 pending" in r.output
