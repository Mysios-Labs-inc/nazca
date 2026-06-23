"""Cost estimation — flat prices, gpt-image-2 token estimates, actual-from-usage."""

import pytest
from PIL import Image

from nazca import cost
from nazca.backends import get_backend


def _png(path):
    Image.new("RGB", (8, 8), "white").save(path)
    return str(path)


# --------------------------------------------------------------------------- flat prices
@pytest.mark.parametrize(
    "model,expected",
    [
        ("imagen-4-fast", 0.02),
        ("imagen-4", 0.04),
        ("nano-banana", 0.039),
        ("seedream", 0.035),
        ("flux-schnell", 0.003),
    ],
)
def test_flat_prices(model, expected):
    est = cost.estimate_image_cost(model)
    assert est is not None
    assert est.usd == expected
    assert est.approx is True  # flat prices still drift → labelled approximate


def test_nano_banana_pro_tiers_on_size():
    assert cost.estimate_image_cost("nano-banana-pro", size="2K").usd == 0.134
    assert cost.estimate_image_cost("nano-banana-pro", size="1K").usd == 0.134


# --------------------------------------------------------------------------- plan cost
def test_estimate_plan_cost_sums_priced_steps():
    pc = cost.estimate_plan_cost([
        {"model": "nano-banana"},      # 0.039
        {"model": "imagen-4"},         # 0.04
        {"model": "flux-schnell"},     # 0.003
    ])
    assert pc.total_usd == round(0.039 + 0.04 + 0.003, 4)
    assert pc.priced == 3 and pc.unpriced == 0


def test_estimate_plan_cost_applies_count():
    pc = cost.estimate_plan_cost([{"model": "nano-banana", "count": 10}])
    assert pc.total_usd == round(0.039 * 10, 4)
    assert pc.priced == 10


def test_estimate_plan_cost_counts_unpriced_without_guessing():
    pc = cost.estimate_plan_cost([
        {"model": "nano-banana"},          # priced
        {"model": "vertex:raw-id"},        # unknown → unpriced
        {"model": "upscale"},              # fal modify op → no flat price → unpriced
    ])
    assert pc.priced == 1
    assert pc.unpriced == 2
    assert pc.total_usd == 0.039  # unpriced steps don't inflate the total


def test_plan_cost_label_flags_unpriced():
    pc = cost.estimate_plan_cost([{"model": "nano-banana"}, {"model": "vertex:raw"}])
    label = pc.label()
    assert label.startswith("~$")
    assert "1 priced, 1 unpriced" in label


def test_plan_cost_label_clean_when_all_priced():
    pc = cost.estimate_plan_cost([{"model": "nano-banana"}])
    assert pc.label() == "~$0.039"
    assert "priced" not in pc.label()


def test_estimate_plan_cost_empty_is_zero():
    pc = cost.estimate_plan_cost([])
    assert pc.total_usd == 0 and pc.priced == 0 and pc.unpriced == 0


# --------------------------------------------------------------------------- video prices
def test_estimate_video_cost_veo_standard_per_second():
    # veo-3.1 @720p no-audio = $0.20/s × 8s = $1.60
    est = cost.estimate_video_cost("veo-3.1", duration=8, resolution="720p", audio=False)
    assert est is not None and est.usd == 1.6
    assert est.approx is True


def test_estimate_video_cost_audio_costs_more():
    no_audio = cost.estimate_video_cost("veo-3.1-fast", duration=8, resolution="1080p", audio=False).usd
    with_audio = cost.estimate_video_cost("veo-3.1-fast", duration=8, resolution="1080p", audio=True).usd
    assert with_audio > no_audio  # 0.12/s vs 0.10/s


def test_estimate_video_cost_scales_with_duration():
    four = cost.estimate_video_cost("veo-3.1-lite", duration=4, resolution="720p").usd
    eight = cost.estimate_video_cost("veo-3.1-lite", duration=8, resolution="720p").usd
    assert eight == round(2 * four, 4)


def test_estimate_video_cost_lite_4k_falls_back():
    # veo-3.1-lite has no 4k tier → falls back to 1080p rate, still priced (not None)
    est = cost.estimate_video_cost("veo-3.1-lite", duration=8, resolution="4k", audio=False)
    assert est is not None
    assert est.usd == round(0.05 * 8, 4)  # 1080p no-audio rate


def test_estimate_video_cost_unpriced_models_return_none():
    # fal / ModelArk video + edit ops have unverified pricing → not guessed
    for m in ("wan-2.6", "seedance-pro", "reframe", "vertex:raw-id", None):
        assert cost.estimate_video_cost(m, duration=8) is None


def test_estimate_plan_cost_mixes_image_and_video():
    pc = cost.estimate_plan_cost([
        {"model": "nano-banana"},                                  # 0.039 image
        {"model": "veo-3.1", "duration": 8, "resolution": "720p"},  # 1.60 video
        {"model": "seedance-pro", "duration": 8},                   # unpriced video
    ])
    assert pc.priced == 2
    assert pc.unpriced == 1
    assert pc.total_usd == round(0.039 + 1.6, 4)
    assert cost.estimate_image_cost("nano-banana-pro", size="4K").usd == 0.24


def test_unknown_model_returns_none():
    assert cost.estimate_image_cost("some-raw-id") is None
    assert cost.estimate_image_cost(None) is None
    assert cost.estimate_image_cost("upscale") is None  # fal modify op, no flat price here


# --------------------------------------------------------------------------- gpt-image-2 tokens
def test_gpt_image_high_matches_published_tokens():
    # high 1024x1024 ≈ 4160 out-tokens × $30/1M
    est = cost.estimate_image_cost("gpt-image-2", aspect_size="1024x1024", quality="high")
    assert est.approx is True
    assert est.usd == pytest.approx(4160 * 30 / 1_000_000)


def test_gpt_image_quality_scales():
    high = cost.estimate_image_cost("gpt-image-2", aspect_size="1024x1024", quality="high").usd
    medium = cost.estimate_image_cost("gpt-image-2", aspect_size="1024x1024", quality="medium").usd
    low = cost.estimate_image_cost("gpt-image-2", aspect_size="1024x1024", quality="low").usd
    assert medium == pytest.approx(high / 4)
    assert low == pytest.approx(high / 16)


def test_gpt_image_unknown_size_falls_back_to_1024():
    est = cost.estimate_image_cost("gpt-image-2", aspect_size="auto", quality="high")
    assert est.usd == pytest.approx(4160 * 30 / 1_000_000)


# --------------------------------------------------------------------------- actual from usage
def test_cost_from_usage_is_not_approximate():
    est = cost.cost_from_openai_usage({"output_tokens": 4160, "input_tokens": 50})
    assert est is not None
    assert est.approx is False
    assert est.usd == pytest.approx(4160 * 30 / 1_000_000 + 50 * 5 / 1_000_000)


def test_cost_from_usage_handles_missing():
    assert cost.cost_from_openai_usage(None) is None
    assert cost.cost_from_openai_usage({}) is None


def test_label_formats():
    assert cost.CostEstimate(0.05, approx=True).label() == "~$0.05"
    assert cost.CostEstimate(0.04, approx=False).label() == "$0.04"


# --------------------------------------------------------------------------- dry-run plan field
def test_dry_run_plan_includes_est_cost_openai(tmp_path):
    from nazca import image

    plan = image.generate_image(
        tmp_path / "o.png", "a cat", model="gpt-image-2",
        aspect_ratio="1:1", quality="high", dry_run=True,
    )
    assert plan["est_cost_usd"] == pytest.approx(4160 * 30 / 1_000_000, rel=1e-3)
    assert not (tmp_path / "o.png").exists()


def test_dry_run_plan_includes_est_cost_seedream(tmp_path):
    from nazca import image

    plan = image.generate_image(
        tmp_path / "o.png", "a cat", ref=[_png(tmp_path / "r.png")],
        model="seedream", aspect_ratio="1:1", size="2K", dry_run=True,
    )
    assert plan["est_cost_usd"] == 0.035


# --------------------------------------------------------------------------- actual cost label
def test_image_cost_label_uses_backend_usage():
    from nazca import image

    backend = get_backend("openai")
    backend.last_usage = {"output_tokens": 6240, "input_tokens": 0}
    label = image.image_cost_label("gpt-image-2", aspect_ratio="9:16", quality="high")
    # actual cost from usage → no "~" prefix
    assert label == cost.cost_from_openai_usage(backend.last_usage).label()
    assert not label.startswith("~")
    backend.last_usage = None  # don't leak into other tests


def test_image_cost_label_flat_model():
    from nazca import image

    assert image.image_cost_label("imagen-4") == "~$0.04"
