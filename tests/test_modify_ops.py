"""P3 behavior: source-image modify ops (upscale / bg_remove) via fal."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from PIL import Image

from nazca import capabilities as cap
from nazca.backends.fal import FalBackend
from nazca.cli import cli
from nazca.image import ImageError, modify_image


def _png(p):
    Image.new("RGB", (16, 16)).save(p)
    return str(p)


# --------------------------------------------------------------------------- caps + inference
def test_modify_models_have_caps():
    assert cap.CAPS["upscale"].ops == frozenset({"upscale"})
    assert cap.CAPS["rmbg"].ops == frozenset({"bg_remove"})


def test_infer_image_op_modify_flags_win():
    assert cap.infer_image_op(0, upscale=True) == "upscale"
    assert cap.infer_image_op(2, bg_remove=True) == "bg_remove"  # modify wins over refs
    assert cap.infer_image_op(1) == "i2i"  # unchanged when no modify flag


def test_models_supporting_modify_ops():
    assert cap.models_supporting("upscale") == ["upscale"]
    assert cap.models_supporting("bg_remove") == ["rmbg"]


# --------------------------------------------------------------------------- modify_image dispatch
def test_modify_upscale_dry_run(tmp_path):
    plan = modify_image(tmp_path / "o.png", _png(tmp_path / "s.png"), op="upscale", upscale_factor=4, dry_run=True)
    assert plan["model"] == "fal-ai/clarity-upscaler"
    assert plan["op"] == "upscale"
    assert plan["body"]["upscale_factor"] == 4
    assert plan["body"]["image_url"].startswith("<data-uri ")  # base64 summarized
    assert not (tmp_path / "o.png").exists()


def test_modify_bg_remove_dry_run(tmp_path):
    plan = modify_image(tmp_path / "o.png", _png(tmp_path / "s.png"), op="bg_remove", dry_run=True)
    assert plan["model"] == "fal-ai/birefnet/v2"
    assert plan["body"]["output_format"] == "png"
    assert "upscale_factor" not in plan["body"]


def test_modify_rejects_non_fal_model(tmp_path):
    with pytest.raises(ImageError, match="needs a fal model"):
        modify_image(tmp_path / "o.png", _png(tmp_path / "s.png"), op="upscale", model="nano-banana", dry_run=True)


def test_modify_real_send_writes_bytes(tmp_path, monkeypatch):
    # mock the fal queue dispatch end to end
    monkeypatch.setattr(FalBackend, "auth_token", lambda self: "key")
    monkeypatch.setattr(FalBackend, "submit_and_download", lambda self, url, body, token, media_type="image": b"UPSCALED")
    out = tmp_path / "o.png"
    res = modify_image(out, _png(tmp_path / "s.png"), op="upscale")
    assert res == out and out.read_bytes() == b"UPSCALED"


# --------------------------------------------------------------------------- fal singular-image extraction
def test_fal_submit_and_download_accepts_singular_image(monkeypatch):
    b = FalBackend()
    monkeypatch.setattr(b, "post", lambda url, body, token: {"status_url": "s", "response_url": "r"})
    seq = iter([{"status": "COMPLETED"}, {"image": {"url": "http://cdn/x.png"}}])  # singular `image`
    monkeypatch.setattr(b, "_get", lambda url, token: next(seq))
    import nazca.backends.fal as falmod
    monkeypatch.setattr(falmod.config, "POLL_INTERVAL", 0)

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"IMGBYTES"

    monkeypatch.setattr(falmod.urllib.request, "urlopen", lambda req: _R())
    raw = b.submit_and_download("u", {}, "key", media_type="image")
    assert raw == b"IMGBYTES"


# --------------------------------------------------------------------------- CLI
def test_cli_upscale_dry_run(tmp_path):
    r = CliRunner().invoke(cli, ["image", _png(tmp_path / "s.png"), "-o", str(tmp_path / "o.png"), "--upscale", "--scale", "3", "--dry-run"])
    assert r.exit_code == 0, r.output
    plan = json.loads(r.output)
    assert plan["op"] == "upscale" and plan["body"]["upscale_factor"] == 3


def test_cli_rmbg_dry_run(tmp_path):
    r = CliRunner().invoke(cli, ["image", _png(tmp_path / "s.png"), "-o", str(tmp_path / "o.png"), "--rmbg", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["op"] == "bg_remove"


def test_cli_upscale_without_source_errors(tmp_path):
    r = CliRunner().invoke(cli, ["image", "-o", str(tmp_path / "o.png"), "--upscale"])
    assert r.exit_code == 2
    assert "needs a SOURCE" in r.output


def test_cli_upscale_and_rmbg_conflict(tmp_path):
    r = CliRunner().invoke(cli, ["image", _png(tmp_path / "s.png"), "-o", str(tmp_path / "o.png"), "--upscale", "--rmbg"])
    assert r.exit_code == 2
    assert "choose one" in r.output


def test_cli_ref_with_upscale_errors(tmp_path):
    r = CliRunner().invoke(cli, ["image", _png(tmp_path / "s.png"), "-o", str(tmp_path / "o.png"), "--upscale", "--ref", _png(tmp_path / "r.png")])
    assert r.exit_code == 2
    assert "--ref is not used" in r.output


def test_cli_source_without_modify_flag_errors(tmp_path):
    # a positional SOURCE with a gen op is a usage mistake → clear error
    r = CliRunner().invoke(cli, ["image", _png(tmp_path / "s.png"), "-o", str(tmp_path / "o.png"), "-p", "a cat"])
    assert r.exit_code == 2
    assert "only for --upscale/--rmbg" in r.output


def test_cli_gen_still_requires_prompt(tmp_path):
    r = CliRunner().invoke(cli, ["image", "-o", str(tmp_path / "o.png")])
    assert r.exit_code == 2
    assert "prompt is required" in r.output


def test_cli_validate_rejects_upscale_on_gen_model(tmp_path):
    # explicit gen model can't upscale → caught up front
    r = CliRunner().invoke(cli, ["image", _png(tmp_path / "s.png"), "-o", str(tmp_path / "o.png"), "--upscale", "--model", "nano-banana"])
    assert r.exit_code == 2
    assert "does not support 'upscale'" in r.output


def test_cli_t2i_unaffected(tmp_path, monkeypatch):
    from nazca import config
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")
    r = CliRunner().invoke(cli, ["image", "-o", str(tmp_path / "o.png"), "-p", "a cat", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["api"] == "gemini"  # normal gen path untouched
