"""Golden regression anchors for Atlas slug composition + dry-run plan bodies.

These pin the *structure* of what the Atlas backend would POST, so a future
"verification" edit (swapping UNVERIFIED field names for real ones) has a clear
diff, and so the standalone-slug refactor can't silently regress.
"""

from __future__ import annotations

from PIL import Image

from nazca.backends.atlas import _STANDALONE_STEMS, _model_slug
from nazca.models import AUDIO_MODELS, MODELS, THREED_MODELS, VIDEO_MODELS
from nazca.request import AudioRequest, ImageRequest, ThreeDRequest, VideoRequest


def _atlas():
    from nazca.backends import get_backend

    return get_backend("atlas")


# --------------------------------------------------------------------------- slug composition
def test_standalone_stems_are_declared_not_sniffed():
    """_STANDALONE_STEMS must be exactly the provider_ids of standalone_slug specs."""
    declared = {
        spec.provider_id
        for reg in (MODELS, VIDEO_MODELS, AUDIO_MODELS, THREED_MODELS)
        for spec in reg.values()
        if spec.standalone_slug
    }
    assert _STANDALONE_STEMS == declared
    assert "xai/tts-v1" in declared and "atlascloud/video-upscaler" in declared


def test_slug_suffix_vs_standalone():
    # suffix appended for normal stems
    assert _model_slug("bytedance/seedance-2.0", "i2v", "text-to-video") == "bytedance/seedance-2.0/image-to-video"
    assert _model_slug("google/veo3.1", "keyframe", "text-to-video") == "google/veo3.1/start-end-frame-to-video"
    assert _model_slug("youchuan/v8.1", "style", "text-to-image") == "youchuan/v8.1/style-transfer"
    # standalone stems pass through untouched, regardless of op
    assert _model_slug("atlascloud/video-upscaler", "video_upscale", "x") == "atlascloud/video-upscaler"
    assert _model_slug("kwaivgi/kling-effects", "effects", "x") == "kwaivgi/kling-effects"
    assert _model_slug("xai/tts-v1", "tts", "x") == "xai/tts-v1"


def test_every_atlas_model_slug_is_stable():
    """Every atlas model resolves to either its stem (standalone) or stem/suffix."""
    reg = {**MODELS, **VIDEO_MODELS, **AUDIO_MODELS, **THREED_MODELS}
    for sh, spec in reg.items():
        if not sh.startswith("atlas-"):
            continue
        for op in spec.ops:
            slug = _model_slug(spec.provider_id, op, "x")
            if spec.standalone_slug:
                assert slug == spec.provider_id, f"{sh}: standalone slug changed"
            else:
                assert slug.startswith(spec.provider_id + "/"), f"{sh}/{op}: missing suffix"


# --------------------------------------------------------------------------- dry-run plan bodies
def test_image_plan_body_keys():
    plan = _atlas().run_image(
        "google/nano-banana-2", "atlas", "",
        ImageRequest(prompt="x", size="1K", aspect_ratio="1:1", dry_run=True),
    )
    assert plan["model"] == "google/nano-banana-2/text-to-image"
    assert set(plan["body"]) == {"model", "prompt", "size", "aspect_ratio"}


def test_video_plan_body_keys(tmp_path):
    img = tmp_path / "s.png"
    Image.new("RGB", (16, 16), (1, 2, 3)).save(img)
    plan = _atlas().run_video(
        "bytedance/seedance-2.0-mini", "",
        VideoRequest(prompt="x", start=str(img), duration=5, dry_run=True),
    )
    assert plan["model"] == "bytedance/seedance-2.0-mini/image-to-video"
    assert {"model", "prompt", "duration", "aspect_ratio", "resolution", "image_url"} <= set(plan["body"])


def test_audio_plan_body_keys():
    plan = _atlas().run_audio("xai/tts-v1", AudioRequest(text="hi", output_format="mp3", dry_run=True))
    assert plan["model"] == "xai/tts-v1"
    assert {"model", "text", "format"} <= set(plan["body"])


def test_threed_plan_body_keys():
    plan = _atlas().run_3d("tencent/hunyuan3d-rapid", ThreeDRequest(prompt="a car", op="t23d", dry_run=True))
    assert plan["model"] == "tencent/hunyuan3d-rapid/text-to-3d"
    assert {"model", "prompt"} <= set(plan["body"])
