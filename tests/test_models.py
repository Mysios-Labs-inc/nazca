"""Registry parity tests — the CI guard that was missing.

Verifies that:
  - Every image/video model in the registry has valid tier, non-empty ops, and a
    price_usd entry or an explicit None (no silent omissions).
  - Cost and capabilities tables have no orphan shorthands that are absent from
    the registry (no models that exist outside the single source of truth).
  - Tiers are all valid values.
  - The derived tables in image.py, video.py, cost.py, and capabilities.py are
    consistent with the canonical registry.
"""

from __future__ import annotations

import pytest

from nazca import capabilities as cap
from nazca import cost
from nazca.image import MODEL_TIERS as IMG_TIERS
from nazca.image import MODELS as IMG_MODELS
from nazca.models import AUDIO_MODELS as AUD_REGISTRY
from nazca.models import MODELS as IMG_REGISTRY
from nazca.models import THREED_MODELS as THREED_REGISTRY
from nazca.models import VALID_TIERS
from nazca.models import VIDEO_MODELS as VID_REGISTRY
from nazca.video import ARK_VIDEO_MODELS, FAL_VIDEO_MODELS, VEO_ALIASES, VIDEO_MODEL_TIERS

# ---------------------------------------------------------------------------
# Key-set parity helpers
# ---------------------------------------------------------------------------

def _all_video_shorthands_in_registry() -> set[str]:
    return set(VID_REGISTRY)


def _priced_shorthands_in_cost() -> set[str]:
    """Models that cost.py has a flat price for (from its _FLAT_USD dict)."""
    # Access the internal _FLAT_USD dict for parity checks.
    return set(cost._FLAT_USD)  # noqa: SLF001


def _all_caps_shorthands() -> set[str]:
    return set(cap.CAPS)


# ---------------------------------------------------------------------------
# Image registry integrity
# ---------------------------------------------------------------------------

def test_every_image_registry_entry_has_valid_tier():
    bad = [sh for sh, spec in IMG_REGISTRY.items() if spec.tier not in VALID_TIERS]
    assert not bad, f"image models with invalid tier: {bad}"


def test_every_image_registry_entry_has_nonempty_ops():
    bad = [sh for sh, spec in IMG_REGISTRY.items() if not spec.ops]
    assert not bad, f"image models with empty ops: {bad}"


def test_every_image_registry_entry_price_is_float_or_none():
    """price_usd must be a float (>0) or explicitly None — never a zero or negative."""
    for sh, spec in IMG_REGISTRY.items():
        if spec.price_usd is not None:
            assert isinstance(spec.price_usd, float), f"{sh}: price_usd must be float, got {type(spec.price_usd)}"
            assert spec.price_usd > 0, f"{sh}: price_usd must be > 0, got {spec.price_usd}"


# ---------------------------------------------------------------------------
# Video registry integrity
# ---------------------------------------------------------------------------

def test_every_video_registry_entry_has_valid_tier():
    bad = [sh for sh, spec in VID_REGISTRY.items() if spec.tier not in VALID_TIERS]
    assert not bad, f"video models with invalid tier: {bad}"


def test_every_video_registry_entry_has_nonempty_ops():
    bad = [sh for sh, spec in VID_REGISTRY.items() if not spec.ops]
    assert not bad, f"video models with empty ops: {bad}"


def test_every_video_registry_ops_are_video_ops():
    for sh, spec in VID_REGISTRY.items():
        bad = spec.ops - cap.VIDEO_OPS
        assert not bad, f"{sh}: non-video ops in video registry: {bad}"


# ---------------------------------------------------------------------------
# Cost table parity — no orphan shorthands in cost._FLAT_USD
# ---------------------------------------------------------------------------

def test_cost_flat_prices_are_subset_of_image_registry():
    """Every model in cost._FLAT_USD must exist in the image registry."""
    known = set(IMG_REGISTRY)
    orphans = _priced_shorthands_in_cost() - known
    assert not orphans, f"cost._FLAT_USD has shorthands absent from image registry: {orphans}"


def test_flat_prices_match_registry_price_usd():
    """cost._FLAT_USD values must match the registry price_usd."""
    for sh, flat in cost._FLAT_USD.items():  # noqa: SLF001
        spec = IMG_REGISTRY.get(sh)
        assert spec is not None, f"cost._FLAT_USD has {sh!r} absent from image registry"
        assert spec.price_usd == flat, (
            f"{sh}: cost._FLAT_USD={flat} but registry price_usd={spec.price_usd}"
        )


# ---------------------------------------------------------------------------
# Capabilities parity — no orphan shorthands in CAPS
# ---------------------------------------------------------------------------

def test_caps_image_shorthands_are_subset_of_image_registry():
    """Every image model in CAPS must exist in the image registry."""
    image_caps = {sh for sh, c in cap.CAPS.items() if c.produces == "image"}
    orphans = image_caps - set(IMG_REGISTRY)
    assert not orphans, f"CAPS has image shorthands absent from image registry: {orphans}"


def test_caps_video_shorthands_are_subset_of_video_registry():
    """Every video model in CAPS must exist in the video registry."""
    video_caps = {sh for sh, c in cap.CAPS.items() if c.produces == "video"}
    orphans = video_caps - _all_video_shorthands_in_registry()
    assert not orphans, f"CAPS has video shorthands absent from video registry: {orphans}"


def test_caps_ops_match_registry_ops():
    """For every shorthand in CAPS, the ops frozenset must match the registry."""
    for sh, caps_entry in cap.CAPS.items():
        spec = (
            IMG_REGISTRY.get(sh)
            or VID_REGISTRY.get(sh)
            or AUD_REGISTRY.get(sh)
            or THREED_REGISTRY.get(sh)
        )
        assert spec is not None, f"CAPS has {sh!r} absent from all registries"
        assert caps_entry.ops == spec.ops, (
            f"{sh}: CAPS.ops={caps_entry.ops} but registry.ops={spec.ops}"
        )


# ---------------------------------------------------------------------------
# Derived table parity — image.py
# ---------------------------------------------------------------------------

def test_image_models_keys_match_registry():
    assert set(IMG_MODELS) == set(IMG_REGISTRY)


def test_image_model_tiers_keys_match_registry():
    assert set(IMG_TIERS) == set(IMG_REGISTRY)


def test_image_model_tiers_values_match_registry():
    for sh in IMG_REGISTRY:
        assert IMG_TIERS[sh] == IMG_REGISTRY[sh].tier, (
            f"{sh}: IMG_TIERS={IMG_TIERS[sh]!r} but registry.tier={IMG_REGISTRY[sh].tier!r}"
        )


def test_image_models_tuple_matches_registry():
    for sh, (pid, region, api, backend) in IMG_MODELS.items():
        spec = IMG_REGISTRY[sh]
        assert pid == spec.provider_id, f"{sh}: provider_id mismatch"
        assert region == spec.region, f"{sh}: region mismatch"
        assert api == spec.api, f"{sh}: api mismatch"
        assert backend == spec.backend, f"{sh}: backend mismatch"


# ---------------------------------------------------------------------------
# Derived table parity — video.py
# ---------------------------------------------------------------------------

def test_veo_aliases_keys_are_vertex_models():
    vertex_keys = {sh for sh, spec in VID_REGISTRY.items() if spec.backend == "vertex"}
    assert set(VEO_ALIASES) == vertex_keys


def test_veo_aliases_values_match_registry():
    for sh, pid in VEO_ALIASES.items():
        assert VID_REGISTRY[sh].provider_id == pid, f"{sh}: VEO_ALIASES value mismatch"


def test_fal_video_models_values_match_registry():
    for sh, pid in FAL_VIDEO_MODELS.items():
        assert VID_REGISTRY[sh].provider_id == pid, f"{sh}: FAL_VIDEO_MODELS value mismatch"


def test_ark_video_models_values_match_registry():
    for sh, pid in ARK_VIDEO_MODELS.items():
        assert VID_REGISTRY[sh].provider_id == pid, f"{sh}: ARK_VIDEO_MODELS value mismatch"


def test_video_model_tiers_keys_match_registry():
    assert set(VIDEO_MODEL_TIERS) == set(VID_REGISTRY)


def test_video_model_tiers_values_match_registry():
    for sh in VID_REGISTRY:
        assert VIDEO_MODEL_TIERS[sh] == VID_REGISTRY[sh].tier, (
            f"{sh}: VIDEO_MODEL_TIERS={VIDEO_MODEL_TIERS[sh]!r} but registry.tier={VID_REGISTRY[sh].tier!r}"
        )


# ---------------------------------------------------------------------------
# All-registry completeness
# ---------------------------------------------------------------------------

def test_all_image_registry_models_have_caps():
    missing = [sh for sh in IMG_REGISTRY if sh not in cap.CAPS]
    assert not missing, f"image registry models missing CAPS: {missing}"


def test_all_video_registry_models_have_caps():
    missing = [sh for sh in VID_REGISTRY if sh not in cap.CAPS]
    assert not missing, f"video registry models missing CAPS: {missing}"


def test_registry_tiers_are_valid():
    for sh, spec in {**IMG_REGISTRY, **VID_REGISTRY}.items():
        assert spec.tier in VALID_TIERS, f"{sh}: invalid tier {spec.tier!r}"


@pytest.mark.parametrize("shorthand", list(IMG_REGISTRY))
def test_image_model_has_ops(shorthand):
    spec = IMG_REGISTRY[shorthand]
    assert spec.ops, f"{shorthand}: ops must be non-empty"
    assert spec.ops <= cap.IMAGE_OPS, f"{shorthand}: ops contain non-image ops: {spec.ops - cap.IMAGE_OPS}"


@pytest.mark.parametrize("shorthand", list(VID_REGISTRY))
def test_video_model_has_ops(shorthand):
    spec = VID_REGISTRY[shorthand]
    assert spec.ops, f"{shorthand}: ops must be non-empty"
    assert spec.ops <= cap.VIDEO_OPS, f"{shorthand}: ops contain non-video ops: {spec.ops - cap.VIDEO_OPS}"


# ---------------------------------------------------------------------------
# Backend verification marker — is_verified()
# ---------------------------------------------------------------------------

from nazca.models import is_verified  # noqa: E402


def test_vertex_backend_is_verified():
    assert is_verified("vertex") is True


def test_openai_backend_is_verified():
    assert is_verified("openai") is True


def test_atlas_backend_is_not_verified():
    assert is_verified("atlas") is False


def test_fal_backend_is_not_verified():
    assert is_verified("fal") is False


def test_modelark_backend_is_not_verified():
    assert is_verified("modelark") is False


def test_atlas_image_model_is_unverified():
    """Every atlas model in the image registry must be flagged unverified."""
    atlas_specs = [spec for spec in IMG_REGISTRY.values() if spec.backend == "atlas"]
    assert atlas_specs, "expected at least one atlas image model in registry"
    for spec in atlas_specs:
        assert not is_verified(spec.backend), (
            f"{spec.shorthand}: atlas backend should be unverified"
        )


def test_vertex_image_model_is_verified():
    """Every vertex model in the image registry must be flagged verified."""
    vertex_specs = [spec for spec in IMG_REGISTRY.values() if spec.backend == "vertex"]
    assert vertex_specs, "expected at least one vertex image model in registry"
    for spec in vertex_specs:
        assert is_verified(spec.backend), (
            f"{spec.shorthand}: vertex backend should be verified"
        )
