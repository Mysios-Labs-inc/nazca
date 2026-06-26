"""Model capabilities — what each model *accepts* and *produces*, as data.

The generation modality (inputs → output) is the axis that decides routing, which
flags are even legal, and where validation belongs. Today that knowledge is
implicit — split between which command you ran (`image` vs `video`) and per-model
runtime checks (imagen raising on `--ref` mid-call). This module makes it
explicit: every model declares the set of operations it supports, so the CLI can
derive the requested op from the flags you passed and validate it up front.

This is the P1 spine — pure data + the canonical vocabulary. It changes no
behavior yet; `image.py`/`video.py` dispatch is unchanged. P2 routes validation
through `CAPS`; later phases add the missing ops as new vocabulary entries.

See docs/media-modalities.md for the human-facing map.
"""

from __future__ import annotations

from dataclasses import dataclass

from nazca.models import MODELS as _MODEL_REGISTRY
from nazca.models import VIDEO_MODELS as _VIDEO_REGISTRY


def _ops(shorthand: str) -> frozenset[str]:
    """Look up the ops frozenset for *shorthand* from the canonical registry."""
    spec = _MODEL_REGISTRY.get(shorthand) or _VIDEO_REGISTRY.get(shorthand)
    if spec is None:
        raise KeyError(f"No ModelSpec for shorthand {shorthand!r} in models registry")
    return spec.ops

# --------------------------------------------------------------------------- vocabulary
# Canonical operations, defined purely by input signature → output. A closed set:
# adding a modality means adding an entry here (and a body-builder on the backends
# that support it), not a new ad-hoc code path.
IMAGE_OPS: frozenset[str] = frozenset(
    {
        "t2i",       # text                  → image
        "i2i",       # text + ref[1]         → image   (restyle / edit)
        "compose",   # text + ref[2..N]      → image   (multi-subject blend)
        "inpaint",   # source + mask + text  → image
        "outpaint",  # source (+text)        → image   (extend canvas)
        "upscale",   # source                → image
        "bg_remove", # source                → image+alpha
    }
)
VIDEO_OPS: frozenset[str] = frozenset(
    {
        "t2v",       # text                  → video
        "i2v",       # text + start          → video
        "keyframe",  # text + start + end    → video   (first-last interpolation)
        "v2v",       # source video (+text)  → video   (restyle / motion-transfer)
        "reframe",   # source video + aspect → video
        "extend",    # source video          → video
    }
)
OPS: frozenset[str] = IMAGE_OPS | VIDEO_OPS

# Which ops imply which inputs — used by the (future) CLI op-inference and by the
# coverage test that keeps this module honest.
OPS_NEEDING_REFS: frozenset[str] = frozenset({"i2i", "compose"})
OPS_NEEDING_SOURCE_IMAGE: frozenset[str] = frozenset({"inpaint", "outpaint", "upscale", "bg_remove"})
OPS_NEEDING_START: frozenset[str] = frozenset({"i2v", "keyframe"})
OPS_NEEDING_END: frozenset[str] = frozenset({"keyframe"})
OPS_NEEDING_SOURCE_VIDEO: frozenset[str] = frozenset({"v2v", "reframe", "extend"})

# --------------------------------------------------------------------------- ref roles
# What a reference image *is* to the model, not just that one was passed. Today refs
# are positional/untyped — `i2i` (1 ref) vs `compose` (2+) is inferred by count alone,
# and the backend blends them with no notion of subject-vs-style-vs-identity. This
# vocabulary is the spine for changing that. Closed set; `ref` is the generic,
# backward-compatible default (a bare `--ref x.png` is role `ref` and behaves exactly
# as today). Like CAPS itself, P1 is descriptive: it declares the vocabulary and which
# models accept which roles. The CLI surface (`--ref path:role`) and per-role backend
# routing land together in a later phase so the role actually changes output.
REF_ROLES: frozenset[str] = frozenset(
    {
        "ref",       # generic / untyped reference — current behavior (default)
        "subject",   # the primary thing to keep or edit (the source content)
        "style",     # match this aesthetic / look, not its content
        "identity",  # this face / character / wordmark — preserve identity
    }
)
DEFAULT_REF_ROLE = "ref"


@dataclass(frozen=True)
class Caps:
    """What a model can do. `ops` is the authority; the rest are constraints.

    `produces`     "image" | "video" (audio deliberately out of scope for now).
    `ops`          the operations this model supports (subset of OPS).
    `max_refs`     ceiling for i2i/compose refs; None = supported, count unpinned.
    `ref_roles`    which REF_ROLES this model accepts. Every ref-capable model accepts
                   the generic `ref` role (current behavior); models that genuinely
                   take semantically-distinct references (e.g. Gemini multi-ref:
                   subject + style + wordmark) additionally declare the typed roles.
                   Authority for role validation; per-role backend routing is later.
    `note`         short caveat (e.g. unverified id, activation needed).
    """

    produces: str
    ops: frozenset[str]
    max_refs: int | None = None
    ref_roles: frozenset[str] = frozenset({DEFAULT_REF_ROLE})
    note: str = ""

    def supports(self, op: str) -> bool:
        return op in self.ops

    def accepts_role(self, role: str) -> bool:
        return role in self.ref_roles


def _img(shorthand: str, **kw) -> Caps:
    """Build an image Caps entry, pulling ops from the canonical model registry."""
    return Caps(produces="image", ops=_ops(shorthand), **kw)


def _vid(shorthand: str, **kw) -> Caps:
    """Build a video Caps entry, pulling ops from the canonical model registry."""
    return Caps(produces="video", ops=_ops(shorthand), **kw)


# --------------------------------------------------------------------------- registry
# Keyed by the same shorthands as image.MODELS / video.*_MODELS. Encodes each
# model's CURRENT capability as nazca drives it today (P1 is descriptive, not
# aspirational) — so the descriptor exposes today's mismatches rather than hiding
# them. The clearest example: `wan-2.6` is text-to-video (its fal id literally
# ends `/text-to-video`), yet `nazca video` forces a start frame — encoded here
# as t2v, which is what P2 will use to stop forcing the start.
#
# ops are sourced from the canonical nazca.models registry; all other fields
# (max_refs, ref_roles, note) live here as capabilities-specific metadata.
CAPS: dict[str, Caps] = {
    # --- Vertex Gemini image: text-to-image + reference image-to-image ---
    "nano-banana":     _img("nano-banana",     ref_roles=REF_ROLES, note="2.5-flash-image; ref/edit, count unpinned"),
    "nano-banana-2":   _img("nano-banana-2",   ref_roles=REF_ROLES, note="3.1-flash-image; ref/edit, count unpinned"),
    "nano-banana-pro": _img("nano-banana-pro", max_refs=14, ref_roles=REF_ROLES, note="3-pro-image; up to 14 refs, legible text"),
    # --- Vertex Imagen: text-to-image ONLY (rejects refs — encoded, not runtime) ---
    "imagen-4-fast":   _img("imagen-4-fast"),
    "imagen-4":        _img("imagen-4"),
    "imagen-3":        _img("imagen-3"),
    # --- fal FLUX: text-to-image + single-ref image-to-image (FLUX takes one ref) ---
    "flux-schnell":    _img("flux-schnell",    max_refs=1, note="fal id unverified; single ref only"),
    "flux-2-dev":      _img("flux-2-dev",      max_refs=1, note="fal id unverified; single ref only"),
    # --- ModelArk Seedream: t2i + native multi-ref i2i; group-image is a separate axis ---
    "seedream":        _img("seedream",        max_refs=14, ref_roles=REF_ROLES, note="needs BytePlus activation; 'group' (N/call) not wired"),
    # --- OpenAI gpt-image-2: t2i (/images/generations) + ref edits (/images/edits, ≤5).
    #     Legible text / ad creative; --quality is the cost/speed lever; token-billed. ---
    "gpt-image-2":     _img("gpt-image-2",     max_refs=5, ref_roles=REF_ROLES, note="OpenAI; legible text/ads; --quality lever; token-billed"),
    # --- fal modify ops (source image → image; ids verified against fal.ai 2026-06-22) ---
    # --- Atlas Cloud image (async media API; schema unverified, dry-run safe) ---
    "atlas-gpt-image-2":     _img("atlas-gpt-image-2",     ref_roles=REF_ROLES, note="Atlas; openai/gpt-image-2; schema unverified"),
    "atlas-nano-banana-2":   _img("atlas-nano-banana-2",   ref_roles=REF_ROLES, note="Atlas; google/nano-banana-2; schema unverified"),
    "atlas-seedream-5-lite": _img("atlas-seedream-5-lite", note="Atlas; bytedance/seedream-v5.0-lite; schema unverified"),
    "atlas-flux-2-pro":      _img("atlas-flux-2-pro",      max_refs=1, note="Atlas; black-forest-labs/flux-2-pro; schema unverified"),
    "atlas-qwen-image-2":    _img("atlas-qwen-image-2",    note="Atlas; qwen/qwen-image-2.0; schema unverified"),
    "atlas-mai-2.5":         _img("atlas-mai-2.5",         note="Atlas; microsoft/mai-image-2.5; schema unverified"),
    "atlas-image-upscaler":  _img("atlas-image-upscaler",  note="Atlas; atlascloud/image-upscaler; schema unverified"),
    "upscale":         _img("upscale",         note="fal clarity-upscaler ($0.03/MP); --scale 1-4"),
    "rmbg":            _img("rmbg",            note="fal birefnet/v2 → transparent PNG (free compute)"),
    "inpaint":         _img("inpaint",         note="fal flux-pro/v1/fill ($0.05/MP); needs --mask (white=edit) + prompt"),
    "outpaint":        _img("outpaint",        note="fal flux-2-pro/outpaint; --expand px/side, no prompt/mask"),
    # --- Vertex Veo: text-to-video, image-to-video (start), keyframe (start+end).
    #     P2 made --start optional and wires the start-less t2v body, so t2v is now
    #     driven (the instance simply drops the `image` field). ---
    "veo-3.1-lite":    _vid("veo-3.1-lite"),
    "veo-3.1-fast":    _vid("veo-3.1-fast"),
    "veo-3.1":         _vid("veo-3.1"),
    # --- fal video ---
    "seedance-2-fast": _vid("seedance-2-fast", note="fal id unverified"),
    "wan-2.6":         _vid("wan-2.6",         note="fal id is .../text-to-video — t2v, NOT i2v (current command mismatch)"),
    # --- ModelArk Seedance i2v variants ---
    "seedance-pro":    _vid("seedance-pro",    note="needs BytePlus activation"),
    "seedance-lite":   _vid("seedance-lite",   note="needs BytePlus activation"),
    # --- fal video-edit (source VIDEO → video; URL-only source). reframe id +
    #     video_url field verified via research workflow (fal.ai 2026-06-22).
    #     v2v/extend: id verified, but the `video_url` input field is fal's
    #     convention and was NOT independently re-confirmed — UNVERIFIED, dry-run
    #     safe; verify the field with a live call before real spend. ---
    # --- Atlas Cloud video (async media API; schema unverified, dry-run safe).
    #     ref2v/motion_control/avatar land with the OPS-vocabulary extension PR. ---
    "atlas-seedance-2-mini": _vid("atlas-seedance-2-mini", note="Atlas; bytedance/seedance-2.0-mini; $0.056/s; schema unverified"),
    "atlas-seedance-2":      _vid("atlas-seedance-2",      note="Atlas; bytedance/seedance-2.0; $0.112/s; schema unverified"),
    "atlas-kling-v3-turbo":  _vid("atlas-kling-v3-turbo",  note="Atlas; kwaivgi/kling-v3.0-turbo; $0.095/s; schema unverified"),
    "atlas-wan-2.7-video":   _vid("atlas-wan-2.7-video",   note="Atlas; alibaba/wan-2.7; $0.10/s; schema unverified"),
    "atlas-veo-3.1":         _vid("atlas-veo-3.1",         note="Atlas; google/veo3.1; $0.20/s; schema unverified"),
    "atlas-hailuo-02":       _vid("atlas-hailuo-02",       note="Atlas; minimax/hailuo-02; $0.10-0.49/s; schema unverified"),
    "reframe":         _vid("reframe",         note="fal luma ray-2/reframe; --aspect target; SOURCE = video URL"),
    "v2v":             _vid("v2v",             note="fal wan-vace-apps/video-edit; prompt required; video_url field UNVERIFIED"),
    "extend":          _vid("extend",          note="fal pixverse/extend; prompt + --duration 5|8; video_url field UNVERIFIED"),
    "atlas-ernie-image-turbo": _img("atlas-ernie-image-turbo", note="Atlas; ERNIE-Image-Turbo; free"),
    "atlas-flux-2-flex": _img("atlas-flux-2-flex", note="Atlas; flux-2-flex; schema unverified"),
    "atlas-flux-dev": _img("atlas-flux-dev", note="Atlas; flux-dev; schema unverified"),
    "atlas-flux-dev-lora": _img("atlas-flux-dev-lora", note="Atlas; flux-dev-lora; schema unverified"),
    "atlas-flux-kontext-dev": _img("atlas-flux-kontext-dev", note="Atlas; flux-kontext-dev; schema unverified"),
    "atlas-flux-kontext-dev-lora": _img("atlas-flux-kontext-dev-lora", note="Atlas; flux-kontext-dev-lora; schema unverified"),
    "atlas-flux-schnell": _img("atlas-flux-schnell", note="Atlas; flux-schnell; schema unverified"),
    "atlas-gpt-image-1": _img("atlas-gpt-image-1", ref_roles=REF_ROLES, note="Atlas; openai/gpt-image-1"),
    "atlas-gpt-image-1-mini": _img("atlas-gpt-image-1-mini", ref_roles=REF_ROLES, note="Atlas; openai/gpt-image-1-mini; budget tier"),
    "atlas-gpt-image-1.5": _img("atlas-gpt-image-1.5", ref_roles=REF_ROLES, note="Atlas; openai/gpt-image-1.5"),
    "atlas-grok-image": _img("atlas-grok-image", note="Atlas; grok-imagine-image; schema unverified"),
    "atlas-grok-image-quality": _img("atlas-grok-image-quality", note="Atlas; grok-imagine-image-quality; schema unverified"),
    "atlas-imagen-3": _img("atlas-imagen-3", note="Atlas; google/imagen3; text-to-image only"),
    "atlas-imagen-3-fast": _img("atlas-imagen-3-fast", note="Atlas; google/imagen3-fast; speed optimization"),
    "atlas-imagen-4": _img("atlas-imagen-4", note="Atlas; google/imagen4; flagship generation"),
    "atlas-imagen-4-fast": _img("atlas-imagen-4-fast", note="Atlas; google/imagen4-fast; faster variant"),
    "atlas-imagen-4-ultra": _img("atlas-imagen-4-ultra", note="Atlas; google/imagen4-ultra; premium quality tier"),
    "atlas-mai-2.5-flash": _img("atlas-mai-2.5-flash", note="Atlas; mai-image-2.5-flash; schema unverified"),
    "atlas-nano-banana": _img("atlas-nano-banana", ref_roles=REF_ROLES, note="Atlas; google/nano-banana; Gemini image base"),
    "atlas-nano-banana-pro": _img("atlas-nano-banana-pro", ref_roles=REF_ROLES, note="Atlas; google/nano-banana-pro; ultra quality variant"),
    "atlas-qwen-image": _img("atlas-qwen-image", note="Atlas; Qwen image; schema unverified"),
    "atlas-qwen-image-2-pro": _img("atlas-qwen-image-2-pro", note="Atlas; Qwen image 2.0 Pro; schema unverified"),
    "atlas-seedream-4": _img("atlas-seedream-4", ref_roles=REF_ROLES, note="Atlas; seedream-v4; rich refs"),
    "atlas-seedream-4.5": _img("atlas-seedream-4.5", ref_roles=REF_ROLES, note="Atlas; seedream-v4.5; rich refs"),
    "atlas-wan-2.5-image": _img("atlas-wan-2.5-image", note="Atlas; Wan 2.5 image; schema unverified"),
    "atlas-wan-2.6-image": _img("atlas-wan-2.6-image", note="Atlas; Wan 2.6 image; schema unverified"),
    "atlas-wan-2.7-image": _img("atlas-wan-2.7-image", note="Atlas; Wan 2.7 image; schema unverified"),
    "atlas-wan-2.7-pro-image": _img("atlas-wan-2.7-pro-image", note="Atlas; Wan 2.7 Pro image; schema unverified"),
    "atlas-youchuan-image": _img("atlas-youchuan-image", note="Atlas; Youchuan v8.1 image; style-transfer dropped"),
    "atlas-z-image-turbo": _img("atlas-z-image-turbo", note="Atlas; z-image turbo; schema unverified"),
    "atlas-grok-imagine-video": _vid("atlas-grok-imagine-video", note="Atlas; xai/grok-imagine-video; schema unverified"),
    "atlas-grok-imagine-video-1.5": _vid("atlas-grok-imagine-video-1.5", note="Atlas; xai/grok-imagine-video-v1.5; schema unverified"),
    "atlas-hailuo-2.3": _vid("atlas-hailuo-2.3", note="Atlas; minimax/hailuo-2.3; schema unverified"),
    "atlas-happyhorse-1.0": _vid("atlas-happyhorse-1.0", note="Atlas; alibaba/happyhorse-1.0; video-edit support"),
    "atlas-happyhorse-1.1": _vid("atlas-happyhorse-1.1", note="Atlas; alibaba/happyhorse-1.1; premium"),
    "atlas-kling-o1": _vid("atlas-kling-o1", note="Atlas; kling-video-o1"),
    "atlas-kling-o3-4k": _vid("atlas-kling-o3-4k", note="Atlas; kling-video-o3-4k"),
    "atlas-kling-o3-pro": _vid("atlas-kling-o3-pro", note="Atlas; kling-video-o3-pro"),
    "atlas-kling-o3-std": _vid("atlas-kling-o3-std", note="Atlas; kling-video-o3-std"),
    "atlas-kling-v1.6-i2v-pro": _vid("atlas-kling-v1.6-i2v-pro", note="Atlas; kling-v1.6-i2v-pro"),
    "atlas-kling-v1.6-i2v-std": _vid("atlas-kling-v1.6-i2v-std", note="Atlas; kling-v1.6-i2v-standard"),
    "atlas-kling-v1.6-multi-i2v-pro": _vid("atlas-kling-v1.6-multi-i2v-pro", note="Atlas; kling-v1.6-multi-i2v-pro"),
    "atlas-kling-v1.6-multi-i2v-std": _vid("atlas-kling-v1.6-multi-i2v-std", note="Atlas; kling-v1.6-multi-i2v-standard"),
    "atlas-kling-v1.6-t2v-std": _vid("atlas-kling-v1.6-t2v-std", note="Atlas; kling-v1.6-t2v-standard"),
    "atlas-kling-v2.0-i2v-master": _vid("atlas-kling-v2.0-i2v-master", note="Atlas; kling-v2.0-i2v-master"),
    "atlas-kling-v2.0-t2v-master": _vid("atlas-kling-v2.0-t2v-master", note="Atlas; kling-v2.0-t2v-master"),
    "atlas-kling-v2.1-i2v-master": _vid("atlas-kling-v2.1-i2v-master", note="Atlas; kling-v2.1-i2v-master"),
    "atlas-kling-v2.1-i2v-pro": _vid("atlas-kling-v2.1-i2v-pro", note="Atlas; kling-v2.1-i2v-pro"),
    "atlas-kling-v2.1-i2v-std": _vid("atlas-kling-v2.1-i2v-std", note="Atlas; kling-v2.1-i2v-standard"),
    "atlas-kling-v2.1-t2v-master": _vid("atlas-kling-v2.1-t2v-master", note="Atlas; kling-v2.1-t2v-master"),
    "atlas-kling-v2.5-turbo-pro": _vid("atlas-kling-v2.5-turbo-pro", note="Atlas; kling-v2.5-turbo-pro"),
    "atlas-kling-v2.6-pro": _vid("atlas-kling-v2.6-pro", note="Atlas; kling-v2.6-pro"),
    "atlas-kling-v3-4k": _vid("atlas-kling-v3-4k", note="Atlas; kling-v3.0-4k"),
    "atlas-kling-v3-pro": _vid("atlas-kling-v3-pro", note="Atlas; kling-v3.0-pro"),
    "atlas-kling-v3-std": _vid("atlas-kling-v3-std", note="Atlas; kling-v3.0-std"),
    "atlas-pixverse-c1": _vid("atlas-pixverse-c1", note="Atlas; pixverse/c1"),
    "atlas-pixverse-v6": _vid("atlas-pixverse-v6", note="Atlas; pixverse/v6; extend support"),
    "atlas-seedance-1-pro": _vid("atlas-seedance-1-pro", note="Atlas; seedance-v1-pro; resolution-baked slugs"),
    "atlas-seedance-1-pro-fast": _vid("atlas-seedance-1-pro-fast", note="Atlas; seedance-v1-pro-fast"),
    "atlas-seedance-1.5-pro": _vid("atlas-seedance-1.5-pro", note="Atlas; seedance-v1.5-pro"),
    "atlas-seedance-2-fast": _vid("atlas-seedance-2-fast", note="Atlas; seedance-2.0-fast"),
    "atlas-van-2.5": _vid("atlas-van-2.5", note="Atlas; atlascloud/van-2.5; schema unverified"),
    "atlas-van-2.6": _vid("atlas-van-2.6", note="Atlas; atlascloud/van-2.6; schema unverified"),
    "atlas-veed-fabric-1.0": _vid("atlas-veed-fabric-1.0", note="Atlas; veed/fabric-1.0; image-to-video only"),
    "atlas-veo-3.1-fast": _vid("atlas-veo-3.1-fast", note="Atlas; google/veo3.1-fast; schema unverified"),
    "atlas-veo-3.1-lite": _vid("atlas-veo-3.1-lite", note="Atlas; google/veo3.1-lite; schema unverified"),
    "atlas-vidu-q1": _vid("atlas-vidu-q1", note="Atlas; vidu/q1; premium"),
    "atlas-vidu-q2": _vid("atlas-vidu-q2", note="Atlas; vidu/q2; base tier"),
    "atlas-vidu-q3": _vid("atlas-vidu-q3", note="Atlas; vidu/q3; base tier"),
    "atlas-wan-2.2-spicy": _vid("atlas-wan-2.2-spicy", note="Atlas; alibaba/wan-2.2-spicy; schema unverified"),
    "atlas-wan-2.2-turbo": _vid("atlas-wan-2.2-turbo", note="Atlas; atlascloud/wan-2.2-turbo; schema unverified"),
    "atlas-wan-2.2-turbo-spicy": _vid("atlas-wan-2.2-turbo-spicy", note="Atlas; atlascloud/wan-2.2-turbo-spicy; schema unverified"),
    "atlas-wan-2.2-video": _vid("atlas-wan-2.2-video", note="Atlas; atlascloud/wan-2.2; schema unverified"),
    "atlas-wan-2.5-video": _vid("atlas-wan-2.5-video", note="Atlas; alibaba/wan-2.5; schema unverified"),
    "atlas-wan-2.6-spicy": _vid("atlas-wan-2.6-spicy", note="Atlas; atlascloud/wan-2.6-spicy; schema unverified"),
    "atlas-wan-2.6-video": _vid("atlas-wan-2.6-video", note="Atlas; alibaba/wan-2.6; schema unverified"),
    "atlas-wan-2.7-spicy": _vid("atlas-wan-2.7-spicy", note="Atlas; atlascloud/wan-2.7-spicy; schema unverified"),
    "atlas-youchuan-v8.1-video": _vid("atlas-youchuan-v8.1-video", note="Atlas; youchuan/v8.1; image-to-video only"),
}


# Stable display order so `nazca models` ops output is deterministic.
_OPS_ORDER = ("t2i", "i2i", "compose", "inpaint", "outpaint", "upscale", "bg_remove",
              "t2v", "i2v", "keyframe", "v2v", "reframe", "extend")


class CapabilityError(ValueError):
    """A requested op isn't supported by the chosen model (raised before dispatch)."""


def infer_image_op(
    n_refs: int = 0,
    *,
    upscale: bool = False,
    bg_remove: bool = False,
    mask: bool = False,
    outpaint: bool = False,
) -> str:
    """Derive the image op from the flags passed.

    Modify signals win over generation: --upscale / --rmbg / --mask (→ inpaint) /
    --outpaint. Otherwise the refs count picks t2i / i2i / compose.
    """
    if upscale:
        return "upscale"
    if bg_remove:
        return "bg_remove"
    if mask:
        return "inpaint"
    if outpaint:
        return "outpaint"
    if n_refs <= 0:
        return "t2i"
    return "i2i" if n_refs == 1 else "compose"


def infer_video_op(
    has_start: bool,
    has_end: bool,
    *,
    reframe: bool = False,
    v2v: bool = False,
    extend: bool = False,
) -> str:
    """Derive the video op from the signals passed.

    Source-video edit signals win (--reframe / --v2v / --extend); otherwise the
    frames pick: none → t2v, start → i2v, +end → keyframe.
    """
    if reframe:
        return "reframe"
    if v2v:
        return "v2v"
    if extend:
        return "extend"
    if not has_start:
        return "t2v"
    return "keyframe" if has_end else "i2v"


def models_supporting(op: str) -> list[str]:
    """Shorthands whose Caps include `op` — used to suggest an alternative model."""
    return sorted(sh for sh, c in CAPS.items() if op in c.ops)


def validate_op(model_shorthand: str | None, op: str, *, n_refs: int = 0) -> None:
    """Raise CapabilityError if `model_shorthand` can't do `op`. No-op for unknown
    models (raw ids, backend:id passthrough, overrides) — we can't know their caps,
    so we don't block.
    """
    if not model_shorthand:
        return
    c = CAPS.get(model_shorthand)
    if c is None:
        return
    if op not in c.ops:
        alts = models_supporting(op)
        hint = f" — try: {', '.join(alts)}" if alts else ""
        raise CapabilityError(
            f"{model_shorthand} does not support '{op}' (it does: {ops_str(model_shorthand)}){hint}"
        )
    if op in OPS_NEEDING_REFS and c.max_refs is not None and n_refs > c.max_refs:
        raise CapabilityError(
            f"{model_shorthand} accepts at most {c.max_refs} reference image(s), got {n_refs}"
        )


def parse_ref(spec: str) -> tuple[str, str]:
    """Split a CLI ref spec `path[:role]` into `(path, role)`.

    A trailing `:role` is recognized only when the suffix is a clean role-like token
    (no slash, dot, or space) — so real paths keep working untouched: `gs://b/x`,
    `C:/x`, and `logo.png` all parse to role `ref`. A role-shaped suffix that isn't a
    known role raises CapabilityError rather than silently mangling the path.
    """
    head, sep, tail = spec.rpartition(":")
    if sep and head and tail and not (set("/. ") & set(tail)):
        if tail not in REF_ROLES:
            raise CapabilityError(
                f"unknown ref role '{tail}' (roles: {', '.join(sorted(REF_ROLES))})"
            )
        return head, tail
    return spec, DEFAULT_REF_ROLE


_ROLE_DESC: dict[str, str] = {
    "subject": "the subject — keep this content",
    "style": "a style reference — apply its look only, not its content",
    "identity": "an identity reference — preserve this face/character/wordmark",
}


def role_annotation(refs: list[tuple[str, str]]) -> str:
    """Build a prompt suffix labelling each typed reference by position, or "" if all
    refs are generic.

    This is the mechanism by which a role changes output: no backend exposes a per-ref
    role field, so refs steer the model through the prompt text. Backward-compatible —
    bare/untyped refs (role `ref`) produce no annotation, so the prompt sent is
    byte-identical to today. `refs` must be in the same order they are passed to the
    backend (positions in the label match the image order).
    """
    labels = [
        f"image {i} is {_ROLE_DESC[role]}"
        for i, (_, role) in enumerate(refs, 1)
        if role != DEFAULT_REF_ROLE and role in _ROLE_DESC
    ]
    if not labels:
        return ""
    return "Reference images, in order: " + "; ".join(labels) + "."


def validate_ref_roles(model_shorthand: str | None, roles: list[str]) -> None:
    """Raise CapabilityError if `model_shorthand` doesn't accept one of `roles`.

    No-op for unknown models (raw ids / passthrough) and for the generic `ref` role,
    which every ref-capable model accepts. Only typed roles are checked.
    """
    if not model_shorthand:
        return
    c = CAPS.get(model_shorthand)
    if c is None:
        return
    for role in roles:
        if role == DEFAULT_REF_ROLE:
            continue
        if role not in c.ref_roles:
            typed = sorted(c.ref_roles - {DEFAULT_REF_ROLE})
            accepts = f"accepts: {', '.join(typed)}" if typed else "takes only untyped refs"
            raise CapabilityError(
                f"{model_shorthand} does not accept ref role '{role}' (it {accepts})"
            )


def caps_for(shorthand: str) -> Caps | None:
    """Return the capability descriptor for a model shorthand, or None if unknown."""
    return CAPS.get(shorthand)


def ops_str(shorthand: str) -> str:
    """Compact, stable ops string for display (e.g. 't2i,i2i,compose')."""
    c = CAPS.get(shorthand)
    if not c:
        return ""
    return ",".join(o for o in _OPS_ORDER if o in c.ops)
