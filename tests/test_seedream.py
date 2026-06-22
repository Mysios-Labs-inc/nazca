"""Tests for ModelArk Seedream ref-to-image (item 2C).

Covers the formerly-dead-code bug: refs were counted but never sent. These lock
in that refs ride the `image` field, that --size/--aspect map to ModelArk's
`size`, and — critically — that the --dry-run plan matches the real POST body
(only base64 blobs are summarized).
"""

from __future__ import annotations

from PIL import Image

from nazca import image as img
from nazca.backends.modelark import ModelArkBackend


def _png(path):
    Image.new("RGB", (16, 16), (123, 80, 40)).save(path)
    return str(path)


# --------------------------------------------------------------------------- size mapping
def test_seedream_size_named_passthrough_without_aspect():
    assert img._seedream_size("2K", None) == "2K"
    assert img._seedream_size("4K", "") == "4K"


def test_seedream_size_maps_aspect_to_pixels():
    # 1:1 at 2K → the square edge.
    assert img._seedream_size("2K", "1:1") == "2048x2048"
    # 16:9 landscape → width > height, aspect preserved, /16 rounded.
    s = img._seedream_size("2K", "16:9")
    w, h = (int(x) for x in s.split("x"))
    assert w > h
    assert w % 16 == 0 and h % 16 == 0
    assert abs((w / h) - (16 / 9)) < 0.02
    # within the documented valid total-pixel range
    assert img._SEEDREAM_MIN_PX <= w * h <= img._SEEDREAM_MAX_PX


def test_seedream_size_portrait_aspect():
    s = img._seedream_size("2K", "9:16")
    w, h = (int(x) for x in s.split("x"))
    assert h > w  # portrait


def test_seedream_size_invalid_aspect_falls_back_to_named():
    assert img._seedream_size("2K", "garbage") == "2K"
    assert img._seedream_size("2K", "0:0") == "2K"


def test_seedream_size_none():
    assert img._seedream_size(None, "16:9") is None


# --------------------------------------------------------------------------- body shape
def test_seedream_body_single_ref_is_string(tmp_path):
    backend = ModelArkBackend()
    body = img._seedream_body("a dish", [_png(tmp_path / "r.png")], "1:1", "2K", backend)
    assert isinstance(body["image"], str)
    assert body["image"].startswith("data:image/png;base64,")
    assert body["sequential_image_generation"] == "disabled"
    assert body["watermark"] is False
    assert body["size"] == "2048x2048"
    # there is NO aspect_ratio field in the Seedream API — must not be sent
    assert "aspect_ratio" not in body


def test_seedream_body_multi_ref_is_array(tmp_path):
    backend = ModelArkBackend()
    refs = [_png(tmp_path / f"r{i}.png") for i in range(3)]
    body = img._seedream_body("blend", refs, None, "2K", backend)
    assert isinstance(body["image"], list)
    assert len(body["image"]) == 3
    assert all(u.startswith("data:image/png;base64,") for u in body["image"])


def test_seedream_body_caps_refs_at_14(tmp_path):
    backend = ModelArkBackend()
    refs = [_png(tmp_path / f"r{i}.png") for i in range(20)]
    body = img._seedream_body("p", refs, None, "2K", backend)
    assert len(body["image"]) == img._SEEDREAM_MAX_REFS == 14


def test_seedream_body_no_refs_omits_image(tmp_path):
    backend = ModelArkBackend()
    body = img._seedream_body("text only", [], None, "2K", backend)
    assert "image" not in body


# --------------------------------------------------------------------------- dispatch + parity
def test_modelark_dry_run_summarizes_image_and_counts_refs(tmp_path):
    out = tmp_path / "o.png"
    plan = img.generate_image(
        out, "a cocktail", ref=[_png(tmp_path / "r.png")], model="seedream",
        aspect_ratio="1:1", size="2K", dry_run=True,
    )
    assert plan["backend"] == "modelark"
    assert plan["refs"] == 1
    # dry-run must NOT leak the full base64 blob
    assert plan["body"]["image"].startswith("<data-uri ")
    assert not out.exists()  # dry-run writes nothing


def test_modelark_dry_run_matches_real_post_body(tmp_path, monkeypatch):
    """Parity: the body actually POSTed equals the dry-run plan body except the
    image field is summarized in the plan. This is the contract that must never
    break."""
    ref = _png(tmp_path / "r.png")

    plan = img.generate_image(
        tmp_path / "dry.png", "a steak", ref=[ref], model="seedream",
        aspect_ratio="16:9", size="2K", dry_run=True,
    )

    captured = {}

    def fake_send(self, model_id, body):
        captured["model_id"] = model_id
        captured["body"] = body
        return b"\x89PNG-bytes"

    monkeypatch.setattr(ModelArkBackend, "generate_image", fake_send)
    # avoid needing a real ARK key — the body is built before auth in this path,
    # but generate_image() (the backend method) is what we patched out.
    out = tmp_path / "real.png"
    result = img.generate_image(
        out, "a steak", ref=[ref], model="seedream",
        aspect_ratio="16:9", size="2K",
    )
    assert result == out
    assert out.read_bytes() == b"\x89PNG-bytes"

    sent = captured["body"]
    # every non-image field is identical between plan and real send
    for k in ("model", "prompt", "sequential_image_generation", "response_format", "watermark", "size"):
        assert sent[k] == plan["body"][k], k
    # image: real send carries the full data-URI, plan carries the summary
    assert sent["image"].startswith("data:image/png;base64,")
    assert plan["body"]["image"].startswith("<data-uri ")
    assert captured["model_id"] == "seedream-4-0-250828"
