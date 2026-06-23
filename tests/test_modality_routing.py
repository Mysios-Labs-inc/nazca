"""P2 behavior: text-to-video dispatch (start-less) + CLI op validation."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from PIL import Image

from nazca import config, video
from nazca.cli import cli


def _png(p):
    Image.new("RGB", (8, 8)).save(p)
    return str(p)


# --------------------------------------------------------------------------- t2v dispatch
def test_t2v_vertex_dry_run_omits_image(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")
    out = tmp_path / "v.mp4"
    res = video.generate_video(out, None, "a slow flythrough", model="veo-3.1", dry_run=True)
    data = json.loads(Path(res).read_text())
    inst = data["instances"][0]
    assert inst["prompt"] == "a slow flythrough"
    assert "image" not in inst  # text-to-video: no start frame
    assert "lastFrame" not in inst


def test_i2v_vertex_dry_run_keeps_image(tmp_path, monkeypatch):
    # regression: start frame still embedded for i2v
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")
    out = tmp_path / "v.mp4"
    res = video.generate_video(out, _png(tmp_path / "s.png"), "pan", model="veo-3.1", dry_run=True)
    data = json.loads(Path(res).read_text())
    assert "image" in data["instances"][0]


def test_t2v_fal_dry_run_omits_image_url(tmp_path):
    out = tmp_path / "v.mp4"
    res = video.generate_video(out, None, "a dancer", model="wan-2.6", dry_run=True)
    data = json.loads(Path(res).read_text())
    assert data["prompt"] == "a dancer"
    assert "image_url" not in data  # t2v: no start image


# --------------------------------------------------------------------------- CLI validation
def test_cli_rejects_imagen_with_ref(tmp_path):
    r = CliRunner().invoke(
        cli,
        ["image", "-o", str(tmp_path / "o.png"), "-p", "x", "--model", "imagen-4", "--ref", _png(tmp_path / "r.png")],
    )
    assert r.exit_code == 2
    assert "imagen-4" in r.output and "i2i" in r.output
    assert "nano-banana" in r.output  # suggestion


def test_cli_rejects_i2v_only_model_without_start(tmp_path):
    r = CliRunner().invoke(cli, ["video", "-o", str(tmp_path / "o.mp4"), "-p", "x", "--model", "seedance-pro"])
    assert r.exit_code == 2
    assert "t2v" in r.output  # seedance-pro can't t2v


def test_cli_rejects_end_without_start(tmp_path):
    r = CliRunner().invoke(
        cli,
        ["video", "-o", str(tmp_path / "o.mp4"), "-p", "x", "--model", "veo-3.1", "--end", _png(tmp_path / "e.png")],
    )
    assert r.exit_code == 2
    assert "--end requires --start" in r.output


def test_cli_allows_compose_on_pro(tmp_path, monkeypatch):
    # nano-banana-pro supports compose (2+ refs) → passes validation, reaches dry-run
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")
    r = CliRunner().invoke(
        cli,
        ["image", "-o", str(tmp_path / "o.png"), "-p", "x", "--model", "nano-banana-pro",
         "--ref", _png(tmp_path / "a.png"), "--ref", _png(tmp_path / "b.png"), "--dry-run"],
    )
    assert r.exit_code == 0, r.output


def test_cli_rejects_flux_multi_ref(tmp_path):
    # 2 refs → compose, which single-ref flux can't do
    r = CliRunner().invoke(
        cli,
        ["image", "-o", str(tmp_path / "o.png"), "-p", "x", "--model", "flux-schnell",
         "--ref", _png(tmp_path / "a.png"), "--ref", _png(tmp_path / "b.png")],
    )
    assert r.exit_code == 2
    assert "compose" in r.output


# --------------------------------------------------------------------------- ref roles (P2/P3)
def test_cli_typed_role_labels_prompt_in_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")
    r = CliRunner().invoke(
        cli,
        ["image", "-o", str(tmp_path / "o.png"), "-p", "make an ad", "--model", "nano-banana",
         "--ref", f"{_png(tmp_path / 'look.png')}:style", "--dry-run"],
    )
    assert r.exit_code == 0, r.output
    plan = json.loads(r.output)
    text = plan["parts"][0]["text"]
    assert "make an ad" in text
    assert "image 1 is a style reference" in text  # role steered into the prompt


def test_cli_bare_ref_leaves_prompt_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")
    r = CliRunner().invoke(
        cli,
        ["image", "-o", str(tmp_path / "o.png"), "-p", "just this", "--model", "nano-banana",
         "--ref", _png(tmp_path / "a.png"), "--dry-run"],
    )
    assert r.exit_code == 0, r.output
    plan = json.loads(r.output)
    assert plan["parts"][0]["text"] == "just this"  # byte-identical: no annotation


def test_cli_rejects_unknown_role(tmp_path):
    r = CliRunner().invoke(
        cli,
        ["image", "-o", str(tmp_path / "o.png"), "-p", "x",
         "--ref", f"{_png(tmp_path / 'a.png')}:bogus", "--dry-run"],
    )
    assert r.exit_code == 2
    assert "unknown ref role" in r.output


def test_cli_rejects_typed_role_on_generic_only_model(tmp_path):
    r = CliRunner().invoke(
        cli,
        ["image", "-o", str(tmp_path / "o.png"), "-p", "x", "--model", "flux-schnell",
         "--ref", f"{_png(tmp_path / 'a.png')}:style", "--dry-run"],
    )
    assert r.exit_code == 2
    assert "does not accept ref role 'style'" in r.output
