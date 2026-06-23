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

import json
from pathlib import Path

from nazca import config
from nazca.backends import get_backend
from nazca.errors import BackendError
from nazca.models import VIDEO_MODELS as _VIDEO_REGISTRY
from nazca.request import VideoRequest


class VeoError(BackendError):
    """Raised for video-generation failures that are not provider-specific."""


def video_cost_label(
    model: str | None,
    *,
    duration: int = 8,
    resolution: str = "720p",
    audio: bool = False,
) -> str | None:
    """Cost line for a Veo clip, e.g. "~$1.6". Returns None when we have no pricing
    (fal/ModelArk video, edit ops, raw ids) — same posture as image_cost_label."""
    from nazca.cost import estimate_video_cost

    est = estimate_video_cost(model, duration=duration, resolution=resolution, audio=audio)
    return est.label() if est is not None else None


# Derived from the canonical registry in nazca.models — do not edit values here;
# edit nazca/models.py instead.

# Shorthand aliases → full Vertex Veo model ids
VEO_ALIASES: dict[str, str] = {
    sh: spec.provider_id
    for sh, spec in _VIDEO_REGISTRY.items()
    if spec.backend == "vertex"
}

# fal video model shorthands → fal model id
# (excludes video-edit ops which are tracked in VIDEO_EDIT_MODELS)
FAL_VIDEO_MODELS: dict[str, str] = {
    sh: spec.provider_id
    for sh, spec in _VIDEO_REGISTRY.items()
    if spec.backend == "fal" and not spec.ops.isdisjoint({"i2v", "t2v"})
}

# ModelArk video model shorthands → BytePlus ModelArk model id
ARK_VIDEO_MODELS: dict[str, str] = {
    sh: spec.provider_id
    for sh, spec in _VIDEO_REGISTRY.items()
    if spec.backend == "modelark"
}

# tier tags: each shorthand → "cheap" | "premium"
VIDEO_MODEL_TIERS: dict[str, str] = {
    sh: spec.tier
    for sh, spec in _VIDEO_REGISTRY.items()
}

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


# fal video-EDIT ops (source VIDEO → video). Shorthand == op name. The source
# enters as a URL (video_url), never inlined — see edit_video(). reframe's id and
# input field are verified (research workflow, fal.ai 2026-06-22); v2v/extend are
# deferred pending a live input-field probe.
# Derived from the canonical registry in nazca.models.
_VIDEO_EDIT_OPS_SET: frozenset[str] = frozenset({"reframe", "v2v", "extend"})
VIDEO_EDIT_MODELS: dict[str, str] = {
    sh: spec.provider_id
    for sh, spec in _VIDEO_REGISTRY.items()
    if not spec.ops.isdisjoint(_VIDEO_EDIT_OPS_SET)
}
VIDEO_EDIT_OPS = tuple(VIDEO_EDIT_MODELS)


def default_video_edit_model(op: str) -> str:
    """Default model shorthand for a video-edit op (the op name is the shorthand)."""
    return op


# Vertex backend name (isolate so future providers stay additive)
VEO_BACKEND = "vertex"


def _resolve_video(model: str) -> tuple[str, str]:
    """Resolve a video model shorthand to (backend_name, model_id).

    Honors the backend:rawid prefix passthrough, the user override file, and the
    built-in registries. Mirrors the previous dispatch order exactly.
    """
    # 1. backend:rawid prefix passthrough
    if ":" in model:
        prefix, raw_id = model.split(":", 1)
        prefix = prefix.lower()
        if prefix in ("vertex", "veo"):
            return ("vertex", raw_id)
        if prefix == "fal":
            return ("fal", raw_id)
        if prefix in ("ark", "modelark"):
            return ("modelark", raw_id)

    # 2. user override file (~/.config/nazca/models.json)
    from nazca.registry import video_override

    ov = video_override(model)
    if ov is not None:
        ov_backend = ov.get("backend", "vertex")
        ov_id = ov.get("id", model)
        if ov_backend in ("fal", "modelark"):
            return (ov_backend, ov_id)
        return ("vertex", ov_id)  # vertex override: raw Veo id

    # 3. built-in registries (fal, then ModelArk, then Vertex aliases)
    if model in FAL_VIDEO_MODELS:
        return ("fal", FAL_VIDEO_MODELS[model])
    if model in ARK_VIDEO_MODELS:
        return ("modelark", ARK_VIDEO_MODELS[model])
    return ("vertex", VEO_ALIASES.get(model, model))


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
    dry_run: bool = False,
) -> Path:
    """Generate a video clip — text-to-video, or from a start frame (+ optional end).

    Vertex Veo: t2v (no frames), i2v (start), or keyframe (start + end).
    fal: start frame as data-URI when given; end/resolution/audio support varies.

    `start=None` produces text-to-video; the per-backend image field is simply
    omitted. (The CLI validates that the chosen model supports the inferred op.)

    Returns the output path (or .request.json for dry-run).
    """
    out = Path(out)
    resolved_model = model or config.VEO_MODEL
    backend_name, model_id = _resolve_video(resolved_model)
    backend = get_backend(backend_name)

    req = VideoRequest(
        prompt=prompt,
        start=str(start) if start is not None else None,
        end=str(end) if end is not None else None,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration=int(duration),
        audio=generate_audio,
        dry_run=dry_run,
    )

    result = backend.run_video(model_id, "", req)

    if dry_run:
        dbg = out.with_suffix(".request.json")
        dbg.write_text(json.dumps(result, indent=2))
        return dbg
    out.write_bytes(result)
    return out


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

    resolved = model or default_video_edit_model(op)
    fal_id = VIDEO_EDIT_MODELS.get(resolved, resolved)  # shorthand → fal id, or raw passthrough
    backend = get_backend("fal")

    req = VideoRequest(
        prompt=prompt or "",
        op=op,
        source=src,
        aspect_ratio=aspect_ratio,
        duration=int(duration),
        dry_run=dry_run,
    )

    result = backend.run_video(fal_id, "", req)
    if dry_run:
        dbg = out.with_suffix(".request.json")
        dbg.write_text(json.dumps(result, indent=2))
        return dbg
    out.write_bytes(result)
    return out
