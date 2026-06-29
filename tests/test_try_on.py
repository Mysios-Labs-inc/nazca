"""Phase 4 tests — Vertex AI Virtual Try-On ("try-on" / try_on op).

Coverage:
  - dry-run plan shape (url ends :predict, model id, products count, no file written)
  - real send (monkeypatched auth + post → bytesBase64Encoded → file written)
  - body shape (_vto_body: personImage b64 str, productImages length)
  - capabilities (validate_op pass / CapabilityError on too many refs or wrong model)
"""

from __future__ import annotations

import base64

import pytest
from PIL import Image

from nazca import capabilities as cap
from nazca import config
from nazca.backends.vertex import VertexBackend
from nazca.image import try_on_image


# --------------------------------------------------------------------------- helpers

def _png(p):
    """Create a tiny solid-colour PNG at *p* and return its str path."""
    Image.new("RGB", (8, 8), color=(128, 64, 32)).save(p)
    return str(p)


# --------------------------------------------------------------------------- dry-run plan

def test_try_on_dry_run_plan_shape(tmp_path, monkeypatch):
    """dry_run=True must return a plan dict — no API call, no output file."""
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")

    person  = _png(tmp_path / "person.png")
    g1      = _png(tmp_path / "g1.png")
    g2      = _png(tmp_path / "g2.png")
    out     = tmp_path / "result.png"

    plan = try_on_image(out, person, [g1, g2], dry_run=True)

    # URL must hit the :predict endpoint
    assert plan["url"].endswith(":predict"), f"unexpected url: {plan['url']}"
    # provider_id in registry
    assert plan["model"] == "virtual-try-on-001"
    # two garments handed in
    assert plan["products"] == 2
    # no file written
    assert not out.exists()


def test_try_on_dry_run_single_garment(tmp_path, monkeypatch):
    """A single garment (non-list) also resolves correctly."""
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")

    plan = try_on_image(
        tmp_path / "r.png",
        _png(tmp_path / "p.png"),
        _png(tmp_path / "g.png"),   # single Path/str, not a list
        dry_run=True,
    )
    assert plan["products"] == 1
    assert plan["model"] == "virtual-try-on-001"


def test_try_on_dry_run_url_contains_project(tmp_path, monkeypatch):
    """The dry-run URL must embed the configured project."""
    monkeypatch.setattr(config, "VERTEX_PROJECT", "my-test-project")

    plan = try_on_image(
        tmp_path / "r.png",
        _png(tmp_path / "p.png"),
        [_png(tmp_path / "g.png")],
        dry_run=True,
    )
    assert "my-test-project" in plan["url"]


# --------------------------------------------------------------------------- real send (mocked)

def test_try_on_real_send_writes_bytes(tmp_path, monkeypatch):
    """Monkeypatching auth + post: result file must contain the decoded bytes."""
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")

    fake_b64 = base64.b64encode(b"TRYON").decode()
    monkeypatch.setattr(VertexBackend, "auth_token", lambda self: "fake-token")
    monkeypatch.setattr(
        VertexBackend,
        "post",
        lambda self, url, body, token: {"predictions": [{"bytesBase64Encoded": fake_b64}]},
    )

    person  = _png(tmp_path / "person.png")
    garment = _png(tmp_path / "garment.png")
    out     = tmp_path / "output.png"

    result = try_on_image(out, person, [garment])

    assert result == out, "must return the output Path"
    assert out.read_bytes() == b"TRYON"


def test_try_on_real_send_multi_garment_writes_bytes(tmp_path, monkeypatch):
    """Multi-garment send still writes bytes from the first prediction."""
    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")

    fake_b64 = base64.b64encode(b"MULTITRYON").decode()
    monkeypatch.setattr(VertexBackend, "auth_token", lambda self: "fake-token")
    monkeypatch.setattr(
        VertexBackend,
        "post",
        lambda self, url, body, token: {"predictions": [{"bytesBase64Encoded": fake_b64}]},
    )

    person = _png(tmp_path / "p.png")
    g1     = _png(tmp_path / "g1.png")
    g2     = _png(tmp_path / "g2.png")
    g3     = _png(tmp_path / "g3.png")
    out    = tmp_path / "out.png"

    result = try_on_image(out, person, [g1, g2, g3])

    assert result == out
    assert out.read_bytes() == b"MULTITRYON"


# --------------------------------------------------------------------------- _vto_body shape

def test_vto_body_person_image_is_nonempty_b64(tmp_path):
    """personImage.image.bytesBase64Encoded must be a non-empty base64 string."""
    backend = VertexBackend()
    person  = _png(tmp_path / "p.png")
    g1      = _png(tmp_path / "g1.png")
    g2      = _png(tmp_path / "g2.png")

    body = backend._vto_body(person, [g1, g2])

    instances = body["instances"]
    assert len(instances) == 1

    person_b64 = instances[0]["personImage"]["image"]["bytesBase64Encoded"]
    assert isinstance(person_b64, str) and len(person_b64) > 0
    # must decode without error
    decoded = base64.b64decode(person_b64)
    assert len(decoded) > 0


def test_vto_body_product_images_count(tmp_path):
    """productImages must have exactly as many entries as garments passed."""
    backend = VertexBackend()
    person  = _png(tmp_path / "p.png")
    g1      = _png(tmp_path / "g1.png")
    g2      = _png(tmp_path / "g2.png")

    body = backend._vto_body(person, [g1, g2])

    product_images = body["instances"][0]["productImages"]
    assert len(product_images) == 2


def test_vto_body_product_images_b64_nonempty(tmp_path):
    """Each productImage.image.bytesBase64Encoded must be a non-empty string."""
    backend = VertexBackend()
    person  = _png(tmp_path / "p.png")
    garments = [_png(tmp_path / f"g{i}.png") for i in range(3)]

    body = backend._vto_body(person, garments)

    for entry in body["instances"][0]["productImages"]:
        b64 = entry["image"]["bytesBase64Encoded"]
        assert isinstance(b64, str) and len(b64) > 0
        base64.b64decode(b64)  # must be valid b64


def test_vto_body_parameters(tmp_path):
    """parameters block must include sampleCount == 1."""
    backend = VertexBackend()
    body = backend._vto_body(_png(tmp_path / "p.png"), [_png(tmp_path / "g.png")])

    assert body["parameters"]["sampleCount"] == 1


# --------------------------------------------------------------------------- capabilities

def test_try_on_caps_registered():
    """'try-on' must be present in CAPS with the try_on op and max_refs=4."""
    c = cap.CAPS["try-on"]
    assert "try_on" in c.ops
    assert c.max_refs == 4


def test_validate_op_try_on_passes_with_two_garments():
    """validate_op must not raise for 'try-on' + 'try_on' with 2 refs."""
    cap.validate_op("try-on", "try_on", n_refs=2)  # no exception


def test_validate_op_try_on_passes_with_four_garments():
    """validate_op must allow up to 4 garments (max_refs=4)."""
    cap.validate_op("try-on", "try_on", n_refs=4)  # no exception


def test_validate_op_try_on_rejects_five_garments():
    """validate_op must raise CapabilityError when n_refs > 4."""
    with pytest.raises(cap.CapabilityError):
        cap.validate_op("try-on", "try_on", n_refs=5)


def test_validate_op_wrong_model_raises():
    """validate_op must raise CapabilityError when the model has no try_on op."""
    with pytest.raises(cap.CapabilityError):
        cap.validate_op("nano-banana", "try_on")


def test_models_supporting_try_on_includes_try_on_model():
    """models_supporting('try_on') must include 'try-on'."""
    assert "try-on" in cap.models_supporting("try_on")
