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


@dataclass(frozen=True)
class Caps:
    """What a model can do. `ops` is the authority; the rest are constraints.

    `produces`     "image" | "video" (audio deliberately out of scope for now).
    `ops`          the operations this model supports (subset of OPS).
    `max_refs`     ceiling for i2i/compose refs; None = supported, count unpinned.
    `note`         short caveat (e.g. unverified id, activation needed).
    """

    produces: str
    ops: frozenset[str]
    max_refs: int | None = None
    note: str = ""

    def supports(self, op: str) -> bool:
        return op in self.ops


def _img(ops, **kw) -> Caps:
    return Caps(produces="image", ops=frozenset(ops), **kw)


def _vid(ops, **kw) -> Caps:
    return Caps(produces="video", ops=frozenset(ops), **kw)


# --------------------------------------------------------------------------- registry
# Keyed by the same shorthands as image.MODELS / video.*_MODELS. Encodes each
# model's CURRENT capability as nazca drives it today (P1 is descriptive, not
# aspirational) — so the descriptor exposes today's mismatches rather than hiding
# them. The clearest example: `wan-2.6` is text-to-video (its fal id literally
# ends `/text-to-video`), yet `nazca video` forces a start frame — encoded here
# as t2v, which is what P2 will use to stop forcing the start.
CAPS: dict[str, Caps] = {
    # --- Vertex Gemini image: text-to-image + reference image-to-image ---
    "nano-banana":     _img({"t2i", "i2i", "compose"}, note="2.5-flash-image; ref/edit, count unpinned"),
    "nano-banana-2":   _img({"t2i", "i2i", "compose"}, note="3.1-flash-image; ref/edit, count unpinned"),
    "nano-banana-pro": _img({"t2i", "i2i", "compose"}, max_refs=14, note="3-pro-image; up to 14 refs, legible text"),
    # --- Vertex Imagen: text-to-image ONLY (rejects refs — encoded, not runtime) ---
    "imagen-4-fast":   _img({"t2i"}),
    "imagen-4":        _img({"t2i"}),
    "imagen-3":        _img({"t2i"}),
    # --- fal FLUX: text-to-image + single-ref image-to-image (FLUX takes one ref) ---
    "flux-schnell":    _img({"t2i", "i2i"}, max_refs=1, note="fal id unverified; single ref only"),
    "flux-2-dev":      _img({"t2i", "i2i"}, max_refs=1, note="fal id unverified; single ref only"),
    # --- ModelArk Seedream: t2i + native multi-ref i2i; group-image is a separate axis ---
    "seedream":        _img({"t2i", "i2i", "compose"}, max_refs=14, note="needs BytePlus activation; 'group' (N/call) not wired"),
    # --- fal modify ops (source image → image; ids verified against fal.ai 2026-06-22) ---
    "upscale":         _img({"upscale"}, note="fal clarity-upscaler ($0.03/MP); --scale 1-4"),
    "rmbg":            _img({"bg_remove"}, note="fal birefnet/v2 → transparent PNG (free compute)"),
    "inpaint":         _img({"inpaint"}, note="fal flux-pro/v1/fill ($0.05/MP); needs --mask (white=edit) + prompt"),
    "outpaint":        _img({"outpaint"}, note="fal flux-2-pro/outpaint; --expand px/side, no prompt/mask"),
    # --- Vertex Veo: text-to-video, image-to-video (start), keyframe (start+end).
    #     P2 made --start optional and wires the start-less t2v body, so t2v is now
    #     driven (the instance simply drops the `image` field). ---
    "veo-3.1-lite":    _vid({"t2v", "i2v", "keyframe"}),
    "veo-3.1-fast":    _vid({"t2v", "i2v", "keyframe"}),
    "veo-3.1":         _vid({"t2v", "i2v", "keyframe"}),
    # --- fal video ---
    "seedance-2-fast": _vid({"i2v"}, note="fal id unverified"),
    "wan-2.6":         _vid({"t2v"}, note="fal id is .../text-to-video — t2v, NOT i2v (current command mismatch)"),
    # --- ModelArk Seedance i2v variants ---
    "seedance-pro":    _vid({"i2v"}, note="needs BytePlus activation"),
    "seedance-lite":   _vid({"i2v"}, note="needs BytePlus activation"),
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


def infer_video_op(has_start: bool, has_end: bool) -> str:
    """Derive the video op from the frames passed: none → t2v, start → i2v, +end → keyframe."""
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


def caps_for(shorthand: str) -> Caps | None:
    """Return the capability descriptor for a model shorthand, or None if unknown."""
    return CAPS.get(shorthand)


def ops_str(shorthand: str) -> str:
    """Compact, stable ops string for display (e.g. 't2i,i2i,compose')."""
    c = CAPS.get(shorthand)
    if not c:
        return ""
    return ",".join(o for o in _OPS_ORDER if o in c.ops)
