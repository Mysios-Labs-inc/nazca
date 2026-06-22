"""Tests for nazca.capabilities — the model capability descriptor (P1).

The load-bearing test is coverage: every model nazca routes to must have a `Caps`
entry, and every declared op must be in the vocabulary and consistent with what
the model produces. That keeps the descriptor honest as models are added.
"""

from __future__ import annotations

from nazca import capabilities as cap
from nazca.image import MODELS as IMG_MODELS
from nazca.video import ARK_VIDEO_MODELS, FAL_VIDEO_MODELS, VEO_ALIASES


def _all_video_shorthands():
    return {*VEO_ALIASES, *FAL_VIDEO_MODELS, *ARK_VIDEO_MODELS}


# --------------------------------------------------------------------------- coverage
def test_every_image_model_has_caps():
    missing = [sh for sh in IMG_MODELS if sh not in cap.CAPS]
    assert not missing, f"image models missing Caps: {missing}"


def test_every_video_model_has_caps():
    missing = [sh for sh in _all_video_shorthands() if sh not in cap.CAPS]
    assert not missing, f"video models missing Caps: {missing}"


def test_no_orphan_caps_entries():
    known = set(IMG_MODELS) | _all_video_shorthands()
    orphans = [sh for sh in cap.CAPS if sh not in known]
    assert not orphans, f"Caps entries for unknown models: {orphans}"


# --------------------------------------------------------------------------- vocabulary integrity
def test_all_declared_ops_are_in_vocabulary():
    for sh, c in cap.CAPS.items():
        bad = c.ops - cap.OPS
        assert not bad, f"{sh} declares unknown ops: {bad}"


def test_produces_matches_op_family():
    for sh, c in cap.CAPS.items():
        if c.produces == "image":
            assert c.ops <= cap.IMAGE_OPS, f"{sh} produces image but has non-image ops"
        elif c.produces == "video":
            assert c.ops <= cap.VIDEO_OPS, f"{sh} produces video but has non-video ops"
        else:
            raise AssertionError(f"{sh} has unexpected produces={c.produces!r}")


def test_image_and_video_op_sets_are_disjoint():
    assert cap.IMAGE_OPS.isdisjoint(cap.VIDEO_OPS)
    assert cap.OPS == cap.IMAGE_OPS | cap.VIDEO_OPS


def test_every_model_supports_at_least_one_op():
    for sh, c in cap.CAPS.items():
        assert c.ops, f"{sh} declares no ops"


# --------------------------------------------------------------------------- specific encodings
def test_imagen_is_t2i_only():
    for sh in ("imagen-3", "imagen-4", "imagen-4-fast"):
        assert cap.CAPS[sh].ops == frozenset({"t2i"})


def test_pro_and_seedream_allow_14_refs():
    assert cap.CAPS["nano-banana-pro"].max_refs == 14
    assert cap.CAPS["seedream"].max_refs == 14


def test_flux_is_single_ref():
    assert cap.CAPS["flux-schnell"].max_refs == 1
    assert "compose" not in cap.CAPS["flux-schnell"].ops  # can't multi-ref


def test_wan_is_t2v_not_i2v():
    # The mismatch the descriptor is meant to expose.
    c = cap.CAPS["wan-2.6"]
    assert c.produces == "video"
    assert c.ops == frozenset({"t2v"})


def test_veo_is_i2v_and_keyframe():
    c = cap.CAPS["veo-3.1"]
    assert "i2v" in c.ops and "keyframe" in c.ops


# --------------------------------------------------------------------------- helpers
def test_supports():
    assert cap.CAPS["nano-banana-pro"].supports("i2i")
    assert not cap.CAPS["imagen-4"].supports("i2i")


def test_caps_for_unknown_is_none():
    assert cap.caps_for("does-not-exist") is None


def test_ops_str_is_stable_and_ordered():
    # ordered per _OPS_ORDER, comma-joined; unknown → ""
    assert cap.ops_str("nano-banana-pro") == "t2i,i2i,compose"
    assert cap.ops_str("veo-3.1") == "i2v,keyframe"
    assert cap.ops_str("does-not-exist") == ""
