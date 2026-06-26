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
                capabilities.OPS vocabulary).  models.py is the canonical
                source; capabilities.py reads spec.ops from this registry
                (it imports models, not the reverse).
    """

    shorthand: str
    provider_id: str
    backend: str
    api: str
    region: str = ""
    tier: str = "cheap"
    price_usd: float | None = None
    ops: frozenset[str] = field(default_factory=frozenset)
    #: True when ``provider_id`` is ALREADY the complete provider slug and must NOT
    #: have an operation suffix appended (e.g. standalone Atlas models like
    #: ``atlascloud/video-upscaler`` or resolution-baked slugs). Declared here so the
    #: backend reads a fact instead of sniffing the slug string.
    standalone_slug: bool = False


# ---------------------------------------------------------------------------
# Image model registry — single canonical source.
#
# ops is declared here as the ground truth. capabilities.py reads spec.ops
# from this registry (it imports models, not the reverse); the import
# direction is capabilities → models, with no cycle in either direction.
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
    # --- Atlas Cloud: async media API (image). provider_id is the slug STEM;
    #     the atlas backend appends the op suffix (text-to-image/edit/...).
    #     Starter subset — full ~36-image map in claudedocs/atlas-cloud-model-map.md.
    #     Schema beyond {model,prompt} UNVERIFIED → dry-run safe. ---
    "atlas-gpt-image-2": ModelSpec(
        shorthand="atlas-gpt-image-2",
        provider_id="openai/gpt-image-2",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.009,
        ops=frozenset({"t2i", "i2i", "compose"}),
    ),
    "atlas-nano-banana-2": ModelSpec(
        shorthand="atlas-nano-banana-2",
        provider_id="google/nano-banana-2",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.08,
        ops=frozenset({"t2i", "i2i", "compose"}),
    ),
    "atlas-seedream-5-lite": ModelSpec(
        shorthand="atlas-seedream-5-lite",
        provider_id="bytedance/seedream-v5.0-lite",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.032,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-flux-2-pro": ModelSpec(
        shorthand="atlas-flux-2-pro",
        provider_id="black-forest-labs/flux-2-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.03,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-qwen-image-2": ModelSpec(
        shorthand="atlas-qwen-image-2",
        provider_id="qwen/qwen-image-2.0",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.028,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-mai-2.5": ModelSpec(
        shorthand="atlas-mai-2.5",
        provider_id="microsoft/mai-image-2.5",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.05,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-image-upscaler": ModelSpec(
        shorthand="atlas-image-upscaler",
        provider_id="atlascloud/image-upscaler",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.01,
        ops=frozenset({"upscale"}),
    ),
    "atlas-ernie-image-turbo": ModelSpec(
        shorthand="atlas-ernie-image-turbo",
        provider_id="baidu/ERNIE-Image-Turbo",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,  # "Free" on Atlas; no positive price to record
        ops=frozenset({"t2i"}),
    ),
    "atlas-flux-2-flex": ModelSpec(
        shorthand="atlas-flux-2-flex",
        provider_id="black-forest-labs/flux-2-flex",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.05,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-flux-dev": ModelSpec(
        shorthand="atlas-flux-dev",
        provider_id="black-forest-labs/flux-dev",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.012,
        ops=frozenset({"t2i"}),
    ),
    "atlas-flux-dev-lora": ModelSpec(
        shorthand="atlas-flux-dev-lora",
        provider_id="black-forest-labs/flux-dev-lora",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.015,
        ops=frozenset({"t2i"}),
    ),
    "atlas-flux-kontext-dev": ModelSpec(
        shorthand="atlas-flux-kontext-dev",
        provider_id="black-forest-labs/flux-kontext-dev",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.025,
        ops=frozenset({"i2i"}),
    ),
    "atlas-flux-kontext-dev-lora": ModelSpec(
        shorthand="atlas-flux-kontext-dev-lora",
        provider_id="black-forest-labs/flux-kontext-dev-lora",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.03,
        ops=frozenset({"i2i"}),
    ),
    "atlas-flux-schnell": ModelSpec(
        shorthand="atlas-flux-schnell",
        provider_id="black-forest-labs/flux-schnell",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.003,
        ops=frozenset({"t2i"}),
    ),
    "atlas-gpt-image-1": ModelSpec(
        shorthand="atlas-gpt-image-1",
        provider_id="openai/gpt-image-1",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.009,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-gpt-image-1-mini": ModelSpec(
        shorthand="atlas-gpt-image-1-mini",
        provider_id="openai/gpt-image-1-mini",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.004,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-gpt-image-1.5": ModelSpec(
        shorthand="atlas-gpt-image-1.5",
        provider_id="openai/gpt-image-1.5",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.008,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-grok-image": ModelSpec(
        shorthand="atlas-grok-image",
        provider_id="xai/grok-imagine-image",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.02,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-grok-image-quality": ModelSpec(
        shorthand="atlas-grok-image-quality",
        provider_id="xai/grok-imagine-image-quality",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.05,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-imagen-3": ModelSpec(
        shorthand="atlas-imagen-3",
        provider_id="google/imagen3",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.04,
        ops=frozenset({"t2i"}),
    ),
    "atlas-imagen-3-fast": ModelSpec(
        shorthand="atlas-imagen-3-fast",
        provider_id="google/imagen3-fast",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.02,
        ops=frozenset({"t2i"}),
    ),
    "atlas-imagen-4": ModelSpec(
        shorthand="atlas-imagen-4",
        provider_id="google/imagen4",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.04,
        ops=frozenset({"t2i"}),
    ),
    "atlas-imagen-4-fast": ModelSpec(
        shorthand="atlas-imagen-4-fast",
        provider_id="google/imagen4-fast",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.02,
        ops=frozenset({"t2i"}),
    ),
    "atlas-imagen-4-ultra": ModelSpec(
        shorthand="atlas-imagen-4-ultra",
        provider_id="google/imagen4-ultra",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.06,
        ops=frozenset({"t2i"}),
    ),
    "atlas-mai-2.5-flash": ModelSpec(
        shorthand="atlas-mai-2.5-flash",
        provider_id="microsoft/mai-image-2.5-flash",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.03,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-nano-banana": ModelSpec(
        shorthand="atlas-nano-banana",
        provider_id="google/nano-banana",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.038,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-nano-banana-pro": ModelSpec(
        shorthand="atlas-nano-banana-pro",
        provider_id="google/nano-banana-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.14,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-qwen-image": ModelSpec(
        shorthand="atlas-qwen-image",
        provider_id="alibaba/qwen-image",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-qwen-image-2-pro": ModelSpec(
        shorthand="atlas-qwen-image-2-pro",
        provider_id="qwen/qwen-image-2.0-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-seedream-4": ModelSpec(
        shorthand="atlas-seedream-4",
        provider_id="bytedance/seedream-v4",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.027,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-seedream-4.5": ModelSpec(
        shorthand="atlas-seedream-4.5",
        provider_id="bytedance/seedream-v4.5",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.036,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-wan-2.5-image": ModelSpec(
        shorthand="atlas-wan-2.5-image",
        provider_id="alibaba/wan-2.5",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-wan-2.6-image": ModelSpec(
        shorthand="atlas-wan-2.6-image",
        provider_id="alibaba/wan-2.6",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-wan-2.7-image": ModelSpec(
        shorthand="atlas-wan-2.7-image",
        provider_id="alibaba/wan-2.7",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-wan-2.7-pro-image": ModelSpec(
        shorthand="atlas-wan-2.7-pro-image",
        provider_id="alibaba/wan-2.7-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2i", "i2i"}),
    ),
    "atlas-youchuan-image": ModelSpec(
        shorthand="atlas-youchuan-image",
        provider_id="youchuan/v8.1",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2i", "i2i", "bg_remove", "compose", "style"}),
    ),
    "atlas-z-image-turbo": ModelSpec(
        shorthand="atlas-z-image-turbo",
        provider_id="z-image/turbo",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.005,
        ops=frozenset({"t2i"}),
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
    # --- Atlas Cloud: async media API (video). provider_id is the slug STEM; the
    #     atlas backend appends the op suffix (text-to-video/image-to-video/...).
    #     ref2v/motion_control/avatar deferred to a follow-up PR that extends the
    #     capabilities.OPS vocabulary. Per-second pricing → cost.py (not yet wired),
    #     so price_usd=None. Full ~55-video map in claudedocs/atlas-cloud-model-map.md. ---
    "atlas-seedance-2-mini": ModelSpec(
        shorthand="atlas-seedance-2-mini",
        provider_id="bytedance/seedance-2.0-mini",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "ref2v"}),
    ),
    "atlas-seedance-2": ModelSpec(
        shorthand="atlas-seedance-2",
        provider_id="bytedance/seedance-2.0",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "ref2v"}),
    ),
    "atlas-kling-v3-turbo": ModelSpec(
        shorthand="atlas-kling-v3-turbo",
        provider_id="kwaivgi/kling-v3.0-turbo",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-wan-2.7-video": ModelSpec(
        shorthand="atlas-wan-2.7-video",
        provider_id="alibaba/wan-2.7",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "ref2v"}),
    ),
    "atlas-veo-3.1": ModelSpec(
        shorthand="atlas-veo-3.1",
        provider_id="google/veo3.1",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "keyframe", "ref2v"}),
    ),
    "atlas-hailuo-02": ModelSpec(
        shorthand="atlas-hailuo-02",
        provider_id="minimax/hailuo-02",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-grok-imagine-video": ModelSpec(
        shorthand="atlas-grok-imagine-video",
        provider_id="xai/grok-imagine-video",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "extend", "v2v"}),
    ),
    "atlas-grok-imagine-video-1.5": ModelSpec(
        shorthand="atlas-grok-imagine-video-1.5",
        provider_id="xai/grok-imagine-video-v1.5",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-hailuo-2.3": ModelSpec(
        shorthand="atlas-hailuo-2.3",
        provider_id="minimax/hailuo-2.3",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-happyhorse-1.0": ModelSpec(
        shorthand="atlas-happyhorse-1.0",
        provider_id="alibaba/happyhorse-1.0",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "v2v"}),
    ),
    "atlas-happyhorse-1.1": ModelSpec(
        shorthand="atlas-happyhorse-1.1",
        provider_id="alibaba/happyhorse-1.1",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-kling-o1": ModelSpec(
        shorthand="atlas-kling-o1",
        provider_id="kwaivgi/kling-video-o1",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-kling-o3-4k": ModelSpec(
        shorthand="atlas-kling-o3-4k",
        provider_id="kwaivgi/kling-video-o3-4k",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-kling-o3-pro": ModelSpec(
        shorthand="atlas-kling-o3-pro",
        provider_id="kwaivgi/kling-video-o3-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "ref2v"}),
    ),
    "atlas-kling-o3-std": ModelSpec(
        shorthand="atlas-kling-o3-std",
        provider_id="kwaivgi/kling-video-o3-std",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "v2v"}),
    ),
    "atlas-kling-v1.6-i2v-pro": ModelSpec(
        shorthand="atlas-kling-v1.6-i2v-pro",
        provider_id="kwaivgi/kling-v1.6-i2v-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-kling-v1.6-i2v-std": ModelSpec(
        shorthand="atlas-kling-v1.6-i2v-std",
        provider_id="kwaivgi/kling-v1.6-i2v-standard",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-kling-v1.6-multi-i2v-pro": ModelSpec(
        shorthand="atlas-kling-v1.6-multi-i2v-pro",
        provider_id="kwaivgi/kling-v1.6-multi-i2v-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-kling-v1.6-multi-i2v-std": ModelSpec(
        shorthand="atlas-kling-v1.6-multi-i2v-std",
        provider_id="kwaivgi/kling-v1.6-multi-i2v-standard",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-kling-v1.6-t2v-std": ModelSpec(
        shorthand="atlas-kling-v1.6-t2v-std",
        provider_id="kwaivgi/kling-v1.6-t2v-standard",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v"}),
    ),
    "atlas-kling-v2.0-i2v-master": ModelSpec(
        shorthand="atlas-kling-v2.0-i2v-master",
        provider_id="kwaivgi/kling-v2.0-i2v-master",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-kling-v2.0-t2v-master": ModelSpec(
        shorthand="atlas-kling-v2.0-t2v-master",
        provider_id="kwaivgi/kling-v2.0-t2v-master",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v"}),
    ),
    "atlas-kling-v2.1-i2v-master": ModelSpec(
        shorthand="atlas-kling-v2.1-i2v-master",
        provider_id="kwaivgi/kling-v2.1-i2v-master",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-kling-v2.1-i2v-pro": ModelSpec(
        shorthand="atlas-kling-v2.1-i2v-pro",
        provider_id="kwaivgi/kling-v2.1-i2v-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"i2v", "keyframe"}),
    ),
    "atlas-kling-v2.1-i2v-std": ModelSpec(
        shorthand="atlas-kling-v2.1-i2v-std",
        provider_id="kwaivgi/kling-v2.1-i2v-standard",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-kling-v2.1-t2v-master": ModelSpec(
        shorthand="atlas-kling-v2.1-t2v-master",
        provider_id="kwaivgi/kling-v2.1-t2v-master",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v"}),
    ),
    "atlas-kling-v2.5-turbo-pro": ModelSpec(
        shorthand="atlas-kling-v2.5-turbo-pro",
        provider_id="kwaivgi/kling-v2.5-turbo-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-kling-v2.6-pro": ModelSpec(
        shorthand="atlas-kling-v2.6-pro",
        provider_id="kwaivgi/kling-v2.6-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "motion_control", "avatar"}),
    ),
    "atlas-kling-v3-4k": ModelSpec(
        shorthand="atlas-kling-v3-4k",
        provider_id="kwaivgi/kling-v3.0-4k",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-kling-v3-pro": ModelSpec(
        shorthand="atlas-kling-v3-pro",
        provider_id="kwaivgi/kling-v3.0-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-kling-v3-std": ModelSpec(
        shorthand="atlas-kling-v3-std",
        provider_id="kwaivgi/kling-v3.0-std",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-pixverse-c1": ModelSpec(
        shorthand="atlas-pixverse-c1",
        provider_id="pixverse/c1",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "keyframe"}),
    ),
    "atlas-pixverse-v6": ModelSpec(
        shorthand="atlas-pixverse-v6",
        provider_id="pixverse/v6",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "keyframe", "extend"}),
    ),
    "atlas-seedance-1-pro": ModelSpec(
        shorthand="atlas-seedance-1-pro",
        provider_id="bytedance/seedance-v1-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-seedance-1-pro-fast": ModelSpec(
        shorthand="atlas-seedance-1-pro-fast",
        provider_id="bytedance/seedance-v1-pro-fast",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-seedance-1.5-pro": ModelSpec(
        shorthand="atlas-seedance-1.5-pro",
        provider_id="bytedance/seedance-v1.5-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-seedance-2-fast": ModelSpec(
        shorthand="atlas-seedance-2-fast",
        provider_id="bytedance/seedance-2.0-fast",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-van-2.5": ModelSpec(
        shorthand="atlas-van-2.5",
        provider_id="atlascloud/van-2.5",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-van-2.6": ModelSpec(
        shorthand="atlas-van-2.6",
        provider_id="atlascloud/van-2.6",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-veed-fabric-1.0": ModelSpec(
        shorthand="atlas-veed-fabric-1.0",
        provider_id="veed/fabric-1.0",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-veo-3.1-fast": ModelSpec(
        shorthand="atlas-veo-3.1-fast",
        provider_id="google/veo3.1-fast",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v"}),
    ),
    "atlas-veo-3.1-lite": ModelSpec(
        shorthand="atlas-veo-3.1-lite",
        provider_id="google/veo3.1-lite",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "keyframe"}),
    ),
    "atlas-vidu-q1": ModelSpec(
        shorthand="atlas-vidu-q1",
        provider_id="vidu/q1",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "keyframe"}),
    ),
    "atlas-vidu-q2": ModelSpec(
        shorthand="atlas-vidu-q2",
        provider_id="vidu/q2",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "keyframe"}),
    ),
    "atlas-vidu-q3": ModelSpec(
        shorthand="atlas-vidu-q3",
        provider_id="vidu/q3",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "keyframe"}),
    ),
    "atlas-wan-2.2-spicy": ModelSpec(
        shorthand="atlas-wan-2.2-spicy",
        provider_id="alibaba/wan-2.2-spicy",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v", "extend"}),
    ),
    "atlas-wan-2.2-turbo": ModelSpec(
        shorthand="atlas-wan-2.2-turbo",
        provider_id="atlascloud/wan-2.2-turbo",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-wan-2.2-turbo-spicy": ModelSpec(
        shorthand="atlas-wan-2.2-turbo-spicy",
        provider_id="atlascloud/wan-2.2-turbo-spicy",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-wan-2.2-video": ModelSpec(
        shorthand="atlas-wan-2.2-video",
        provider_id="atlascloud/wan-2.2",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-wan-2.5-video": ModelSpec(
        shorthand="atlas-wan-2.5-video",
        provider_id="alibaba/wan-2.5",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "extend"}),
    ),
    "atlas-wan-2.6-spicy": ModelSpec(
        shorthand="atlas-wan-2.6-spicy",
        provider_id="atlascloud/wan-2.6-spicy",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-wan-2.6-video": ModelSpec(
        shorthand="atlas-wan-2.6-video",
        provider_id="alibaba/wan-2.6",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"t2v", "i2v", "v2v"}),
    ),
    "atlas-wan-2.7-spicy": ModelSpec(
        shorthand="atlas-wan-2.7-spicy",
        provider_id="atlascloud/wan-2.7-spicy",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-youchuan-v8.1-video": ModelSpec(
        shorthand="atlas-youchuan-v8.1-video",
        provider_id="youchuan/v8.1",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"i2v"}),
    ),
    "atlas-video-upscaler": ModelSpec(
        shorthand="atlas-video-upscaler",
        provider_id="atlascloud/video-upscaler",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        standalone_slug=True,
        ops=frozenset({"video_upscale"}),
    ),
    "atlas-kling-effects": ModelSpec(
        shorthand="atlas-kling-effects",
        provider_id="kwaivgi/kling-effects",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        standalone_slug=True,
        ops=frozenset({"effects"}),
    ),
    "atlas-kling-v2.6-std": ModelSpec(
        shorthand="atlas-kling-v2.6-std",
        provider_id="kwaivgi/kling-v2.6-std",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        ops=frozenset({"motion_control", "avatar"}),
    ),
    "atlas-infinitetalk": ModelSpec(
        shorthand="atlas-infinitetalk",
        provider_id="atlascloud/infinitetalk",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        standalone_slug=True,
        ops=frozenset({"avatar"}),
    ),
    "atlas-avatar-omnihuman-1.5": ModelSpec(
        shorthand="atlas-avatar-omnihuman-1.5",
        provider_id="bytedance/avatar-omni-human-v1.5",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        standalone_slug=True,
        ops=frozenset({"avatar"}),
    ),
}

# ---------------------------------------------------------------------------
# Audio model registry (text-to-speech). price_usd is None — TTS is billed per
# 1K characters (handled by cost.estimate_audio_cost).
# ---------------------------------------------------------------------------
AUDIO_MODELS: dict[str, ModelSpec] = {
    "atlas-tts-grok": ModelSpec(
        shorthand="atlas-tts-grok",
        provider_id="xai/tts-v1",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=None,
        standalone_slug=True,
        ops=frozenset({"tts"}),
    ),
    "atlas-tts-elevenlabs-v3": ModelSpec(
        shorthand="atlas-tts-elevenlabs-v3",
        provider_id="elevenlabs/v3",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=None,
        ops=frozenset({"tts"}),
    ),
}


# ---------------------------------------------------------------------------
# 3D model registry (text/image → GLB mesh). price_usd is the flat per-run price.
# ---------------------------------------------------------------------------
THREED_MODELS: dict[str, ModelSpec] = {
    "atlas-hunyuan3d-rapid": ModelSpec(
        shorthand="atlas-hunyuan3d-rapid",
        provider_id="tencent/hunyuan3d-rapid",
        backend="atlas",
        api="atlas",
        region="",
        tier="cheap",
        price_usd=0.02,
        ops=frozenset({"t23d", "i23d"}),
    ),
    "atlas-hunyuan3d-pro": ModelSpec(
        shorthand="atlas-hunyuan3d-pro",
        provider_id="tencent/hunyuan3d-pro",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.02,
        ops=frozenset({"t23d", "i23d"}),
    ),
    "atlas-seed3d-2": ModelSpec(
        shorthand="atlas-seed3d-2",
        provider_id="bytedance/seed3d-v2.0",
        backend="atlas",
        api="atlas",
        region="",
        tier="premium",
        price_usd=0.353,
        ops=frozenset({"i23d"}),
    ),
}


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

VALID_TIERS: frozenset[str] = frozenset({"cheap", "premium"})

# Backends whose cost tables and request schemas have been validated against
# live production APIs.  Absent from this set ⇒ cost/schema is best-effort.
_VERIFIED_BACKENDS: frozenset[str] = frozenset({"vertex", "openai"})


def is_verified(backend: str) -> bool:
    """Return True when *backend* cost and schema are live-verified.

    ``vertex`` and ``openai`` backends are proven against live APIs.
    ``atlas``, ``fal``, and ``modelark`` have not been independently
    verified — their pricing or request schemas may differ from what the
    registry records.
    """
    return backend in _VERIFIED_BACKENDS


def all_image_shorthands() -> list[str]:
    """Return all image model shorthands in insertion order."""
    return list(MODELS)


def all_video_shorthands() -> list[str]:
    """Return all video model shorthands in insertion order."""
    return list(VIDEO_MODELS)


def all_audio_shorthands() -> list[str]:
    """Return all audio model shorthands in insertion order."""
    return list(AUDIO_MODELS)


def all_3d_shorthands() -> list[str]:
    """Return all 3D model shorthands in insertion order."""
    return list(THREED_MODELS)


# ---------------------------------------------------------------------------
# Registry accessor helpers — unified cross-modality API used by the
# orchestrator projection dicts and any future plan-level tooling.
# ---------------------------------------------------------------------------

#: Maps the public modality name to its canonical registry dict.
_REGISTRY_BY_MODALITY: dict[str, dict[str, ModelSpec]] = {
    "image": MODELS,
    "video": VIDEO_MODELS,
    "audio": AUDIO_MODELS,
    "3d":    THREED_MODELS,
}

#: Tier defaults per modality — mirrors the _TIER_DEFAULTS in each orchestrator
#: module (image.py / video.py / audio.py / threed.py).  Centralised here so the
#: orchestrators can stay as the *policy* layer while this module is the *fact* layer.
_TIER_DEFAULTS_BY_MODALITY: dict[str, dict[str, str]] = {
    "image": {"cheap": "nano-banana",          "premium": "nano-banana-pro"},
    "video": {"cheap": "veo-3.1-lite",         "premium": "veo-3.1"},
    "audio": {"cheap": "atlas-tts-grok",       "premium": "atlas-tts-elevenlabs-v3"},
    "3d":    {"cheap": "atlas-hunyuan3d-rapid", "premium": "atlas-hunyuan3d-pro"},
}


def models_for(
    modality: str,
    *,
    backend: str | None = None,
) -> dict[str, ModelSpec]:
    """Return the model registry for *modality*, insertion order preserved.

    Parameters
    ----------
    modality:
        One of ``"image"``, ``"video"``, ``"audio"``, or ``"3d"``.
    backend:
        When provided, only entries whose ``ModelSpec.backend`` matches this
        value are included (e.g. ``"vertex"``, ``"fal"``, ``"atlas"``).

    Raises
    ------
    ValueError
        If *modality* is not one of the four known modalities.
    """
    try:
        registry = _REGISTRY_BY_MODALITY[modality]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY_BY_MODALITY))
        raise ValueError(
            f"Unknown modality {modality!r}. Must be one of: {known}"
        ) from None

    if backend is None:
        return dict(registry)
    return {k: v for k, v in registry.items() if v.backend == backend}


def tiers(modality: str) -> dict[str, str]:
    """Return ``{shorthand: tier}`` for every model in *modality*.

    Mirrors the old ``*_MODEL_TIERS`` projection dicts.  The result preserves
    the registry's insertion order.

    Raises
    ------
    ValueError
        If *modality* is not recognised (delegated to :func:`models_for`).
    """
    return {k: v.tier for k, v in models_for(modality).items()}


def tier_default(modality: str, tier: str | None) -> str | None:
    """Return the default model shorthand for *tier* in *modality*.

    Encodes the same defaults the per-modality orchestrators use
    (``select_model`` / ``select_audio_model`` / ``select_3d_model``).
    Returns ``None`` when *tier* is ``None`` or not a recognised tier value,
    matching the existing orchestrator behaviour.

    Raises
    ------
    ValueError
        If *modality* is not recognised.
    """
    if modality not in _REGISTRY_BY_MODALITY:
        known = ", ".join(sorted(_REGISTRY_BY_MODALITY))
        raise ValueError(
            f"Unknown modality {modality!r}. Must be one of: {known}"
        )
    if tier is None:
        return None
    return _TIER_DEFAULTS_BY_MODALITY[modality].get(tier)
