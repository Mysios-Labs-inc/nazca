"""--format / transparency wiring for gpt-image-2.

Regression guard: gpt-image-2 uses `output_format` (png|jpeg|webp), NOT
`response_format` (a DALL·E-2/3 field it does not accept — sending it 400s).
"""

from nazca.image import generate_image


def _body(tmp_path, **kw):
    plan = generate_image(tmp_path / "o.png", "t", model="gpt-image-2", dry_run=True, **kw)
    return plan["body"]


def test_webp_sets_output_format(tmp_path):
    body = _body(tmp_path, output_format="webp")
    assert body["output_format"] == "webp"
    assert "response_format" not in body  # gpt-image-2 has no such param


def test_jpeg_sets_output_format(tmp_path):
    assert _body(tmp_path, output_format="jpeg")["output_format"] == "jpeg"


def test_png_default_omits_format(tmp_path):
    # png is the model default — no need to send the field
    assert "output_format" not in _body(tmp_path, output_format="png")
    assert "output_format" not in _body(tmp_path)


def test_transparent_sets_background(tmp_path):
    assert _body(tmp_path, transparent=True)["background"] == "transparent"
