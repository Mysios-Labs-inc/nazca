"""Single-source-of-truth model registry for all nazca image and video models.

Every model's metadata lives here exactly once — provider id, backend, api,
region, tier, price, and supported ops. The per-module tables (image.MODELS,
image.MODEL_TIERS, video.VEO_ALIASES, etc.) are derived from these registries;
cost.py and capabilities.py read from them too.

Adding or changing a model means editing ONE place: the MODELS or VIDEO_MODELS
dict below. The derived tables, cost lookups, and capability checks update
automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelSpec:
    """Complete specification for a single model.

    shorthand   the user-facing alias used on the CLI and in plan files.
    provider_id the raw upstream model identifier passed to the backend API.
    backend     dispatch key: "vertex" | "fal" | "modelark" | "openai".
    api         sub-routing within the backend: "gemini" | "imagen" | "fal" |
                "fal-modify" | "modelark" | "openai".
    region      provider region string (vertex only; empty for others).
    tier        "cheap" | "premium" — coarse quality/cost tier.
    price_usd   flat per-image/per-call USD price, or None when billed by
                token/second or unverified (cost module handles those separately).
    ops         frozenset of operation codes this model supports (subset of
                capabilities.OPS vocabulary).  Populated from capabilities.CAPS
                data — kept here so the registry is self-contained.
    """

    shorthand: str
    provider_id: str
    backend: str
    api: str
    region: str = ""
    tier: str = "cheap"
    price_usd: float | None = None
    ops: frozenset[str] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Image model registry — single canonical source.
#
# ops is the frozenset from capabilities.CAPS; values are reproduced here so
# this module has no import dependency on capabilities (which would be circular
# since capabilities imports nothing from models).
# ---------------------------------------------------------------------------
MODELS: dict[str, ModelSpec] = {
    # --- Vertex: Gemini image (supports --ref) ---
    "nano-banana": ModelSpec(
        shorthand="nano-banana",
        provider_id="gemini-2.5-flash-image",
        backend="vertex",
        api="gemini",
        region="us-central1",
        tier="cheap",
        price_usd=0.039,
        ops=frozenset({"t2i", "i2i", "compose"}),
    ),
    "nano-banana-2": ModelSpec(
        shorthand="nano-banana-2",
        provider_id="gemini-3.1-flash-image",
        backend="vertex",
        api="gemini",
        region="global",
        tier="cheap",
        price_usd=0.039,
        ops=frozenset({"t2i", "i2i", "compose"}),
    ),
    "nano-banana-pro": ModelSpec(
        shorthand="nano-banana-pro",
        provider_id="gemini-3-pro-image",
        backend="vertex",
        api="gemini",
        region="global",
        tier="premium",
        # price is size-dependent (0.134 @1K/2K, 0.24 @4K) — None here;
        # cost.py uses the special-case _nano_banana_pro() helper.
        price_usd=None,
        ops=frozenset({"t2i", "i2i", "compose"}),
    ),
    # --- Vertex: Imagen (text-to-image only) ---
    "imagen-4-fast": ModelSpec(
        shorthand="imagen-4-fast",
        provider_id="imagen-4.0-fast-generate-001",
        backend="vertex",
        api="imagen",
        region="us-central1",
        tier="cheap",
        price_usd=0.02,
        ops=frozenset({"t2i"}),
    ),
    "imagen-4": ModelSpec(
        shorthand="imagen-4",
        provider_id="imagen-4.0-generate-001",
        backend="vertex",
        api="imagen",
        region="us-central1",
        tier="premium",
        price_usd=0.04,
        ops=frozenset({"t2i"}),
    ),
    "imagen-3": ModelSpec(
        shorthand="imagen-3",
        provider_id="imagen-3.0-generate-002",
        backend="vertex",
        api="imagen",
        region="us-central1",
        tier="cheap",
        price_usd=0.02,
        ops=frozenset({"t2i"}),
    ),
    # --- fal.ai: FLUX long tail ---
    "flux-schnell": ModelSpec(
        shorthand="flux-schnell",
        provider_id="fal-ai/flux/schnell",
        backend="fal",
        api="fal",
        region="",
        tier="cheap",
        price_usd=0.003,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "flux-2-dev": ModelSpec(
        shorthand="flux-2-dev",
        provider_id="fal-ai/flux/dev",
        backend="fal",
        api="fal",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2i", "i2i"}),
    ),
    # --- ByteDance ModelArk: Seedream ---
    "seedream": ModelSpec(
        shorthand="seedream",
        provider_id="seedream-4-0-250828",
        backend="modelark",
        api="modelark",
        region="",
        tier="cheap",
        price_usd=0.035,
        ops=frozenset({"t2i", "i2i", "compose"}),
    ),
    # --- OpenAI: gpt-image-2 (token-billed) ---
    "gpt-image-2": ModelSpec(
        shorthand="gpt-image-2",
        provider_id="gpt-image-2",
        backend="openai",
        api="openai",
        region="",
        tier="premium",
        # token-billed: price_usd=None; cost.py handles via _estimate_gpt_image()
        price_usd=None,
        ops=frozenset({"t2i", "i2i", "compose"}),
    ),
    # --- fal modify ops (source image → image) ---
    "upscale": ModelSpec(
        shorthand="upscale",
        provider_id="fal-ai/clarity-upscaler",
        backend="fal",
        api="fal-modify",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"upscale"}),
    ),
    "rmbg": ModelSpec(
        shorthand="rmbg",
        provider_id="fal-ai/birefnet/v2",
        backend="fal",
        api="fal-modify",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"bg_remove"}),
    ),
    "inpaint": ModelSpec(
        shorthand="inpaint",
        provider_id="fal-ai/flux-pro/v1/fill",
        backend="fal",
        api="fal-modify",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"inpaint"}),
    ),
    "outpaint": ModelSpec(
        shorthand="outpaint",
        provider_id="fal-ai/flux-2-pro/outpaint",
        backend="fal",
        api="fal-modify",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"outpaint"}),
    ),
}

# ---------------------------------------------------------------------------
# Video model registry — single canonical source.
#
# price_usd is None for all video models: Veo is per-second billed (handled
# by cost.py's _VEO_PER_SEC table), and fal/ModelArk video pricing is
# unverified. The registry carries ops and tier for parity checks.
# ---------------------------------------------------------------------------
VIDEO_MODELS: dict[str, ModelSpec] = {
    # --- Vertex Veo 3.1 ---
    "veo-3.1-lite": ModelSpec(
        shorthand="veo-3.1-lite",
        provider_id="veo-3.1-lite-generate-001",
        backend="vertex",
        api="veo",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "keyframe"}),
    ),
    "veo-3.1-fast": ModelSpec(
        shorthand="veo-3.1-fast",
        provider_id="veo-3.1-fast-generate-001",
        backend="vertex",
        api="veo",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "keyframe"}),
    ),
    "veo-3.1": ModelSpec(
        shorthand="veo-3.1",
        provider_id="veo-3.1-generate-001",
        backend="vertex",
        api="veo",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "keyframe"}),
    ),
    # --- fal video ---
    "seedance-2-fast": ModelSpec(
        shorthand="seedance-2-fast",
        provider_id="fal-ai/bytedance/seedance/v2/lite",
        backend="fal",
        api="fal",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "wan-2.6": ModelSpec(
        shorthand="wan-2.6",
        provider_id="fal-ai/wan/v2.6/text-to-video",
        backend="fal",
        api="fal",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v"}),
    ),
    # --- ModelArk Seedance i2v variants ---
    "seedance-pro": ModelSpec(
        shorthand="seedance-pro",
        provider_id="bytedance-seedance-1-0-pro-250528",
        backend="modelark",
        api="modelark",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "seedance-lite": ModelSpec(
        shorthand="seedance-lite",
        provider_id="bytedance-seedance-1-0-lite-i2v-250428",
        backend="modelark",
        api="modelark",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    # --- fal video-edit ops (source VIDEO → video) ---
    "reframe": ModelSpec(
        shorthand="reframe",
        provider_id="fal-ai/luma-dream-machine/ray-2/reframe",
        backend="fal",
        api="fal",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"reframe"}),
    ),
    "v2v": ModelSpec(
        shorthand="v2v",
        provider_id="fal-ai/wan-vace-apps/video-edit",
        backend="fal",
        api="fal",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"v2v"}),
    ),
    "extend": ModelSpec(
        shorthand="extend",
        provider_id="fal-ai/pixverse/extend",
        backend="fal",
        api="fal",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"extend"}),
    ),
}

# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

VALID_TIERS: frozenset[str] = frozenset({"cheap", "premium"})


def all_image_shorthands() -> list[str]:
    """Return all image model shorthands in insertion order."""
    return list(MODELS)


def all_video_shorthands() -> list[str]:
    """Return all video model shorthands in insertion order."""
    return list(VIDEO_MODELS)
