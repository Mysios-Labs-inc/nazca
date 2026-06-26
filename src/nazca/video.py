"""Video generation — Vertex Veo 3.1, fal.ai (Seedance / Wan long tail), ModelArk (Seedance direct).

This module is a thin orchestrator: it resolves the model shorthand to a backend +
model id, builds a typed `VideoRequest`, and hands it to `backend.run_video(...)`.
Each backend owns its own body-building, polling, extraction, and dry-run plan
rendering — so there is no per-backend branching here.

Vertex path (default, no API key): start frame + optional end frame (keyframe).
fal path (opt-in, FAL_KEY): queue submit/poll/download; plus video-edit ops.
ModelArk path (opt-in, ARK_API_KEY): Seedance async task — schema UNVERIFIED.
"""

from __future__ import annotations

from pathlib import Path

from nazca import config
from nazca.backends import get_backend, require_capability
from nazca.cost import estimate_video_cost
from nazca.errors import VeoError  # noqa: F401  (re-exported for back-compat)
from nazca.media import write_result
from nazca.models import (  # named projections now live in the data layer (re-exported here)
    ARK_VIDEO_MODELS,  # noqa: F401
    ATLAS_VIDEO_MODELS,  # noqa: F401
    FAL_VIDEO_MODELS,  # noqa: F401
    VEO_ALIASES,  # noqa: F401
    VIDEO_EDIT_MODELS,  # noqa: F401
    VIDEO_EDIT_OPS_SET,
    VIDEO_MODEL_TIERS,  # noqa: F401
)
from nazca.models import VIDEO_MODELS as _VIDEO_REGISTRY
from nazca.request import VideoRequest


def video_cost_label(
    model: str | None,
    *,
    duration: int = 8,
    resolution: str = "720p",
    audio: bool = False,
) -> str | None:
    """Cost line for a Veo clip, e.g. "~$1.6". Returns None when we have no pricing
    (fal/ModelArk video, edit ops, raw ids) — same posture as image_cost_label."""
    est = estimate_video_cost(model, duration=duration, resolution=resolution, audio=audio)
    return est.label() if est is not None else None


# The named registry projections (VEO_ALIASES, FAL_VIDEO_MODELS, ARK_VIDEO_MODELS,
# ATLAS_VIDEO_MODELS, VIDEO_MODEL_TIERS, VIDEO_EDIT_MODELS) now live in nazca.models
# beside the registry; they are imported above and re-exported for back-compat.

# tier → default Vertex-direct model (never auto-route to fal)
_TIER_DEFAULTS: dict[str, str] = {
    "cheap":   "veo-3.1-lite",
    "premium": "veo-3.1",
}


def select_model(tier: str | None) -> str | None:
    """Return the default model shorthand for *tier*, or None if tier is None."""
    if tier is None:
        return None
    return _TIER_DEFAULTS.get(tier)


# The OP NAMES that route through edit_video (source VIDEO → video). The CLI tests
# `op in VIDEO_EDIT_OPS`, so this is the op set, not the model shorthands.
# VIDEO_EDIT_OPS_SET is the single source (imported from nazca.models).
VIDEO_EDIT_OPS = tuple(sorted(VIDEO_EDIT_OPS_SET))

# Ops whose shorthand isn't the op name need a default model (fal reframe/v2v/extend
# use op==shorthand; the Atlas-only ops point at a concrete Atlas model).
_EDIT_OP_DEFAULTS: dict[str, str] = {
    "motion_control": "atlas-kling-v2.6-pro",
    "video_upscale": "atlas-video-upscaler",
}


def default_video_edit_model(op: str) -> str:
    """Default model shorthand for a video-edit op (op name == shorthand for fal ops)."""
    return _EDIT_OP_DEFAULTS.get(op, op)


# Vertex backend name (isolate so future providers stay additive)
VEO_BACKEND = "vertex"


def _resolve_video(model: str) -> tuple[str, str]:
    """Resolve a video model shorthand to (backend_name, model_id).

    Delegates to the unified resolver in nazca.resolve; behavior is identical
    to the previous hand-rolled implementation (same prefix table, same override
    lookup, same registry order, same vertex fallback).
    """
    from nazca.resolve import resolve  # local import: avoids circular at module load

    r = resolve(model, "video")
    return (r.backend, r.provider_id)


def generate_video(
    out: str | Path,
    start: str | Path | None,
    prompt: str,
    end: str | Path | None = None,
    *,
    model: str | None = None,
    duration: int = 8,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    generate_audio: bool = False,
    op: str | None = None,
    refs: list[str] | None = None,
    audio_path: str | None = None,
    dry_run: bool = False,
) -> Path:
    """Generate a video clip — text-to-video, or from a start frame (+ optional end).

    Vertex Veo: t2v (no frames), i2v (start), or keyframe (start + end).
    fal: start frame as data-URI when given; end/resolution/audio support varies.

    `start=None` produces text-to-video; the per-backend image field is simply
    omitted. `op` (when given) is the explicit op for backends that encode it in the
    model slug (Atlas: keyframe/effects/ref2v); `refs` carries reference images for
    ref2v. (The CLI validates that the chosen model supports the inferred op.)

    Returns the output path (or .request.json for dry-run).
    """
    from nazca.resolve import resolve  # local import: avoids circular at module load

    out = Path(out)
    resolved_model = model or config.VEO_MODEL
    resolved = resolve(resolved_model, "video")
    backend = require_capability(get_backend(resolved.backend), "video")

    req = VideoRequest(
        prompt=prompt,
        start=str(start) if start is not None else None,
        end=str(end) if end is not None else None,
        refs=[str(r) for r in (refs or [])],
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration=int(duration),
        audio=generate_audio,
        audio_path=audio_path,
        op=op,
        dry_run=dry_run,
    )

    return write_result(out, backend.run_video(resolved, req), dry_run)


def edit_video(
    out: str | Path,
    source: str,
    *,
    op: str,
    model: str | None = None,
    aspect_ratio: str = "9:16",
    prompt: str | None = None,
    duration: int = 8,
    dry_run: bool = False,
) -> Path:
    """Video-edit ops (source VIDEO → video) via fal.

    The source is passed as a URL (`video_url`), NOT inlined as a base64 data-URI
    — a real clip is MB-scale and fal expects a URL. Local-file → fal-storage
    upload is a planned follow-up; for now SOURCE must be a public http(s) URL.

      reframe → fal-ai/luma-dream-machine/ray-2/reframe  {video_url, aspect_ratio}
      v2v     → fal-ai/wan-vace-apps/video-edit          {video_url, prompt}
      extend  → fal-ai/pixverse/extend                   {video_url, prompt, duration}

    NOTE: the `video_url` field for v2v/extend is fal's convention but UNVERIFIED
    live — dry-run safe; verify with a real call before spending.

    Returns the output path (or .request.json for dry-run).
    """
    out = Path(out)
    src = str(source)
    if not (src.startswith("http://") or src.startswith("https://")):
        raise VeoError(
            f"{op} SOURCE must be a public https:// video URL "
            f"(local-file upload to fal storage is a planned follow-up); got: {src}"
        )

    from nazca.resolve import ResolvedModel  # local import: avoids circular at module load

    resolved = model or default_video_edit_model(op)
    edit_id = VIDEO_EDIT_MODELS.get(resolved, resolved)  # shorthand → provider id, or raw passthrough
    spec = _VIDEO_REGISTRY.get(resolved)
    backend_name = spec.backend if spec else "fal"  # per-model backend (fal | atlas)
    backend = require_capability(get_backend(backend_name), "video")
    rm = ResolvedModel(
        shorthand=resolved, provider_id=edit_id, backend=backend_name,
        api="", region="", spec=spec,
    )

    req = VideoRequest(
        prompt=prompt or "",
        op=op,
        source=src,
        aspect_ratio=aspect_ratio,
        duration=int(duration),
        dry_run=dry_run,
    )

    return write_result(out, backend.run_video(rm, req), dry_run)
