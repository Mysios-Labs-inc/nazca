"""Tests for nazca.capabilities — the model capability descriptor (P1).

The load-bearing test is coverage: every model nazca routes to must have a `Caps`
entry, and every declared op must be in the vocabulary and consistent with what
the model produces. That keeps the descriptor honest as models are added.
"""

from __future__ import annotations

import pytest

from nazca import capabilities as cap
from nazca.image import MODELS as IMG_MODELS
from nazca.models import AUDIO_MODELS as _AUDIO_MODELS
from nazca.models import THREED_MODELS as _THREED_MODELS
from nazca.video import (
    ARK_VIDEO_MODELS,
    ATLAS_VIDEO_MODELS,
    FAL_VIDEO_MODELS,
    VEO_ALIASES,
    VIDEO_EDIT_MODELS,
)


def _all_video_shorthands():
    return {*VEO_ALIASES, *FAL_VIDEO_MODELS, *ARK_VIDEO_MODELS, *ATLAS_VIDEO_MODELS, *VIDEO_EDIT_MODELS}


def _all_audio_shorthands():
    return set(_AUDIO_MODELS)


def _all_3d_shorthands():
    return set(_THREED_MODELS)


# --------------------------------------------------------------------------- coverage
def test_every_image_model_has_caps():
    missing = [sh for sh in IMG_MODELS if sh not in cap.CAPS]
    assert not missing, f"image models missing Caps: {missing}"


def test_every_video_model_has_caps():
    missing = [sh for sh in _all_video_shorthands() if sh not in cap.CAPS]
    assert not missing, f"video models missing Caps: {missing}"


def test_every_audio_model_has_caps():
    missing = [sh for sh in _all_audio_shorthands() if sh not in cap.CAPS]
    assert not missing, f"audio models missing Caps: {missing}"


def test_every_3d_model_has_caps():
    missing = [sh for sh in _all_3d_shorthands() if sh not in cap.CAPS]
    assert not missing, f"3D models missing Caps: {missing}"


def test_no_orphan_caps_entries():
    known = set(IMG_MODELS) | _all_video_shorthands() | _all_audio_shorthands() | _all_3d_shorthands()
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
        elif c.produces == "audio":
            assert c.ops <= cap.AUDIO_OPS, f"{sh} produces audio but has non-audio ops"
        elif c.produces == "model3d":
            assert c.ops <= cap.THREED_OPS, f"{sh} produces 3D but has non-3D ops"
        else:
            raise AssertionError(f"{sh} has unexpected produces={c.produces!r}")


def test_op_families_are_disjoint():
    families = [cap.IMAGE_OPS, cap.VIDEO_OPS, cap.AUDIO_OPS, cap.THREED_OPS]
    for i, a in enumerate(families):
        for b in families[i + 1:]:
            assert a.isdisjoint(b)
    assert cap.OPS == cap.IMAGE_OPS | cap.VIDEO_OPS | cap.AUDIO_OPS | cap.THREED_OPS


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
    assert cap.ops_str("veo-3.1") == "t2v,i2v,keyframe"
    assert cap.ops_str("does-not-exist") == ""


# --------------------------------------------------------------------------- op inference (P2)
def test_infer_image_op():
    assert cap.infer_image_op(0) == "t2i"
    assert cap.infer_image_op(1) == "i2i"
    assert cap.infer_image_op(2) == "compose"
    assert cap.infer_image_op(14) == "compose"


def test_infer_video_op():
    assert cap.infer_video_op(False, False) == "t2v"
    assert cap.infer_video_op(True, False) == "i2v"
    assert cap.infer_video_op(True, True) == "keyframe"


def test_veo_now_supports_t2v():
    # P2: start-less Veo body wired → t2v is a declared op.
    for sh in ("veo-3.1", "veo-3.1-fast", "veo-3.1-lite"):
        assert "t2v" in cap.CAPS[sh].ops


def test_models_supporting_t2v():
    supp = cap.models_supporting("t2v")
    assert "wan-2.6" in supp and "veo-3.1" in supp
    assert "seedance-pro" not in supp  # i2v only


# --------------------------------------------------------------------------- validation (P2)
def test_validate_ok():
    cap.validate_op("nano-banana-pro", "i2i", n_refs=3)  # no raise
    cap.validate_op("wan-2.6", "t2v")
    cap.validate_op("veo-3.1", "t2v")


def test_validate_rejects_unsupported_op_with_suggestion():
    with pytest.raises(cap.CapabilityError) as ei:
        cap.validate_op("imagen-4", "i2i", n_refs=1)
    msg = str(ei.value)
    assert "imagen-4" in msg and "i2i" in msg
    assert "nano-banana" in msg  # suggests a model that supports i2i


def test_validate_rejects_i2v_only_model_for_t2v():
    with pytest.raises(cap.CapabilityError) as ei:
        cap.validate_op("seedance-pro", "t2v")
    assert "wan-2.6" in str(ei.value) or "veo" in str(ei.value)


def test_validate_enforces_max_refs():
    # pro supports compose but caps refs at 14 → 15 is rejected on the count
    with pytest.raises(cap.CapabilityError, match="at most 14"):
        cap.validate_op("nano-banana-pro", "compose", n_refs=15)


def test_validate_rejects_compose_on_single_ref_model():
    # flux can't compose at all → rejected on the op, not the count
    with pytest.raises(cap.CapabilityError, match="compose"):
        cap.validate_op("flux-schnell", "compose", n_refs=2)


def test_validate_unknown_model_is_noop():
    # raw ids / backend:id passthrough / overrides → can't know caps, don't block
    cap.validate_op("vertex:some-raw-id", "i2i", n_refs=5)
    cap.validate_op(None, "t2i")


# --------------------------------------------------------------------------- ref roles (P1)
def test_default_ref_role_is_in_vocabulary():
    assert cap.DEFAULT_REF_ROLE in cap.REF_ROLES


def test_every_caps_accepts_the_generic_role():
    # backward compat: a bare `--ref x.png` (role `ref`) is valid on every model
    for sh, c in cap.CAPS.items():
        assert cap.DEFAULT_REF_ROLE in c.ref_roles, f"{sh} rejects the generic ref role"


def test_multi_ref_models_accept_typed_roles():
    for sh in ("nano-banana", "nano-banana-2", "nano-banana-pro", "seedream", "gpt-image-2"):
        c = cap.CAPS[sh]
        assert {"subject", "style", "identity"} <= c.ref_roles, f"{sh} missing typed roles"


def test_single_ref_model_is_generic_only():
    # FLUX takes one untyped ref → no semantic typing claimed
    assert cap.CAPS["flux-schnell"].ref_roles == frozenset({"ref"})


def test_parse_ref_bare_path_is_generic():
    assert cap.parse_ref("hero.png") == ("hero.png", "ref")


def test_parse_ref_typed_suffix():
    assert cap.parse_ref("look.png:style") == ("look.png", "style")
    assert cap.parse_ref("face.png:identity") == ("face.png", "identity")


def test_parse_ref_leaves_real_paths_untouched():
    # colons in real paths must not be mistaken for roles (no role-shaped suffix)
    assert cap.parse_ref("gs://bucket/a.png") == ("gs://bucket/a.png", "ref")
    assert cap.parse_ref("C:/tmp/a.png") == ("C:/tmp/a.png", "ref")


def test_parse_ref_rejects_unknown_role():
    with pytest.raises(cap.CapabilityError, match="unknown ref role 'bogus'"):
        cap.parse_ref("a.png:bogus")


def test_validate_ref_roles_ok():
    cap.validate_ref_roles("nano-banana-pro", ["subject", "style", "identity"])  # no raise
    cap.validate_ref_roles("flux-schnell", ["ref", "ref"])  # generic always ok


def test_validate_ref_roles_rejects_typed_on_generic_only_model():
    with pytest.raises(cap.CapabilityError, match="style"):
        cap.validate_ref_roles("flux-schnell", ["style"])


def test_validate_ref_roles_unknown_model_is_noop():
    cap.validate_ref_roles("vertex:raw-id", ["style"])
    cap.validate_ref_roles(None, ["identity"])


# --------------------------------------------------------------------------- role annotation (P2/P3)
def test_role_annotation_empty_when_all_generic():
    assert cap.role_annotation([("a.png", "ref"), ("b.png", "ref")]) == ""
    assert cap.role_annotation([]) == ""


def test_role_annotation_labels_typed_refs_by_position():
    ann = cap.role_annotation([("hero.png", "subject"), ("look.png", "style"), ("face.png", "identity")])
    assert ann.startswith("Reference images, in order:")
    assert "image 1 is the subject" in ann
    assert "image 2 is a style reference" in ann
    assert "image 3 is an identity reference" in ann
    assert ann.endswith(".")


def test_role_annotation_positions_count_all_refs_not_just_typed():
    # a generic ref still occupies its position; only typed ones are labelled
    ann = cap.role_annotation([("plain.png", "ref"), ("look.png", "style")])
    assert "image 2 is a style reference" in ann
    assert "image 1" not in ann  # the generic ref is not labelled
