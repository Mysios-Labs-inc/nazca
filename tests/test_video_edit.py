"""P4-A: video-edit ops (reframe) via fal, source as a video URL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from nazca import capabilities as cap
from nazca.cli import cli
from nazca.video import VeoError, edit_video

URL = "https://cdn.example.com/clip.mp4"


# --------------------------------------------------------------------------- caps + inference
def test_reframe_cap_and_inference():
    assert cap.CAPS["reframe"].ops == frozenset({"reframe"})
    assert cap.infer_video_op(False, False, reframe=True) == "reframe"
    assert cap.infer_video_op(True, False, reframe=True) == "reframe"  # edit wins over frames
    assert cap.infer_video_op(True, False) == "i2v"  # unchanged when no reframe


def test_reframe_in_models_supporting():
    assert cap.models_supporting("reframe") == ["reframe"]


# --------------------------------------------------------------------------- edit_video dispatch
def test_edit_reframe_dry_run(tmp_path):
    res = edit_video(tmp_path / "o.mp4", URL, op="reframe", aspect_ratio="9:16", dry_run=True)
    data = json.loads(Path(res).read_text())
    assert data["model"] == "fal-ai/luma-dream-machine/ray-2/reframe"
    assert data["op"] == "reframe"
    assert data["video_url"] == URL  # URL passed through, never inlined
    assert data["aspect_ratio"] == "9:16"
    assert "prompt" not in data  # optional, omitted


def test_edit_reframe_optional_prompt(tmp_path):
    res = edit_video(tmp_path / "o.mp4", URL, op="reframe", prompt="fill the edges", dry_run=True)
    assert json.loads(Path(res).read_text())["prompt"] == "fill the edges"


def test_edit_rejects_local_path(tmp_path):
    # P4-A is URL-only; a local file must be rejected (upload is a follow-up)
    with pytest.raises(VeoError, match="https"):
        edit_video(tmp_path / "o.mp4", "/local/clip.mp4", op="reframe", dry_run=True)


def test_edit_real_send_writes_bytes(tmp_path, monkeypatch):
    from nazca.backends.fal import FalBackend
    monkeypatch.setattr(FalBackend, "auth_token", lambda self: "key")
    monkeypatch.setattr(FalBackend, "submit_and_download", lambda self, url, body, token, media_type="video": b"VIDEO")
    out = tmp_path / "o.mp4"
    res = edit_video(out, URL, op="reframe")
    assert res == out and out.read_bytes() == b"VIDEO"


# --------------------------------------------------------------------------- v2v + extend (P4-B)
def test_v2v_extend_caps_and_inference():
    assert cap.CAPS["v2v"].ops == frozenset({"v2v"})
    assert cap.CAPS["extend"].ops == frozenset({"extend"})
    assert cap.infer_video_op(False, False, v2v=True) == "v2v"
    assert cap.infer_video_op(False, False, extend=True) == "extend"
    assert cap.infer_video_op(True, False, v2v=True) == "v2v"  # edit wins over frames


def test_edit_v2v_dry_run(tmp_path):
    res = edit_video(tmp_path / "o.mp4", URL, op="v2v", prompt="make it neon", dry_run=True)
    data = json.loads(Path(res).read_text())
    assert data["model"] == "fal-ai/wan-vace-apps/video-edit"
    assert data["video_url"] == URL and data["prompt"] == "make it neon"


def test_edit_v2v_requires_prompt(tmp_path):
    with pytest.raises(VeoError, match="prompt"):
        edit_video(tmp_path / "o.mp4", URL, op="v2v", dry_run=True)


def test_edit_extend_dry_run(tmp_path):
    res = edit_video(tmp_path / "o.mp4", URL, op="extend", prompt="keep dancing", duration=8, dry_run=True)
    data = json.loads(Path(res).read_text())
    assert data["model"] == "fal-ai/pixverse/extend"
    assert data["duration"] == "8"  # enum string, not int
    assert data["prompt"] == "keep dancing"


def test_edit_extend_rejects_bad_duration(tmp_path):
    with pytest.raises(VeoError, match="5 or 8"):
        edit_video(tmp_path / "o.mp4", URL, op="extend", prompt="x", duration=6, dry_run=True)


def test_edit_v2v_rejects_local_path(tmp_path):
    with pytest.raises(VeoError, match="https"):
        edit_video(tmp_path / "o.mp4", "/local/clip.mp4", op="v2v", prompt="x", dry_run=True)


def test_cli_v2v_dry_run(tmp_path):
    r = CliRunner().invoke(cli, ["video", URL, "-o", str(tmp_path / "o.mp4"), "--v2v", "-p", "neon", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert json.loads(Path(str(tmp_path / "o.request.json")).read_text())["op"] == "v2v"


def test_cli_v2v_without_prompt_errors(tmp_path):
    r = CliRunner().invoke(cli, ["video", URL, "-o", str(tmp_path / "o.mp4"), "--v2v"])
    assert r.exit_code == 2
    assert "needs -p" in r.output


def test_cli_extend_dry_run(tmp_path):
    r = CliRunner().invoke(cli, ["video", URL, "-o", str(tmp_path / "o.mp4"), "--extend", "-p", "more", "--duration", "8", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert json.loads(Path(str(tmp_path / "o.request.json")).read_text())["duration"] == "8"


def test_cli_two_video_edit_ops_conflict(tmp_path):
    r = CliRunner().invoke(cli, ["video", URL, "-o", str(tmp_path / "o.mp4"), "--v2v", "--reframe", "-p", "x"])
    assert r.exit_code == 2
    assert "choose one video-edit op" in r.output


def test_cli_extend_bad_duration_clean_error(tmp_path):
    # a VeoError from edit_video must surface as a clean ❌ + exit 2, not a traceback
    r = CliRunner().invoke(cli, ["video", URL, "-o", str(tmp_path / "o.mp4"), "--extend", "-p", "x", "--duration", "6"])
    assert r.exit_code == 2
    assert "5 or 8" in r.output


def test_cli_edit_local_path_clean_error(tmp_path):
    # URL-only guard via the CLI → clean error, not a traceback
    r = CliRunner().invoke(cli, ["video", "/local/clip.mp4", "-o", str(tmp_path / "o.mp4"), "--reframe"])
    assert r.exit_code == 2
    assert "https" in r.output


# --------------------------------------------------------------------------- CLI
def test_cli_reframe_dry_run(tmp_path):
    r = CliRunner().invoke(cli, ["video", URL, "-o", str(tmp_path / "o.mp4"), "--reframe", "--aspect", "1:1", "--dry-run"])
    assert r.exit_code == 0, r.output
    data = json.loads(Path(str(tmp_path / "o.request.json")).read_text())
    assert data["op"] == "reframe" and data["aspect_ratio"] == "1:1"


def test_cli_reframe_without_source_errors(tmp_path):
    r = CliRunner().invoke(cli, ["video", "-o", str(tmp_path / "o.mp4"), "--reframe"])
    assert r.exit_code == 2
    assert "needs a SOURCE" in r.output


def test_cli_reframe_with_start_conflicts(tmp_path):
    r = CliRunner().invoke(cli, ["video", URL, "-o", str(tmp_path / "o.mp4"), "--reframe", "--start", "s.png"])
    assert r.exit_code == 2
    assert "are for frame ops" in r.output


def test_cli_source_without_reframe_errors(tmp_path):
    r = CliRunner().invoke(cli, ["video", URL, "-o", str(tmp_path / "o.mp4"), "-p", "x"])
    assert r.exit_code == 2
    assert "only for video-edit ops" in r.output


def test_cli_frame_path_still_requires_prompt(tmp_path):
    r = CliRunner().invoke(cli, ["video", "-o", str(tmp_path / "o.mp4")])
    assert r.exit_code == 2
    assert "prompt is required" in r.output


def test_cli_t2v_unaffected(tmp_path, monkeypatch):
    from nazca import config
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")
    r = CliRunner().invoke(cli, ["video", "-o", str(tmp_path / "o.mp4"), "-p", "a flythrough", "--model", "veo-3.1", "--dry-run"])
    assert r.exit_code == 0, r.output
    data = json.loads(Path(str(tmp_path / "o.request.json")).read_text())
    assert "instances" in data and "image" not in data["instances"][0]  # t2v unchanged


def test_cli_validate_rejects_reframe_on_veo(tmp_path):
    r = CliRunner().invoke(cli, ["video", URL, "-o", str(tmp_path / "o.mp4"), "--reframe", "--model", "veo-3.1"])
    assert r.exit_code == 2
    assert "does not support 'reframe'" in r.output
