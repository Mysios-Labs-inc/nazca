"""Image generation — Vertex AI (Gemini / Imagen), fal.ai (FLUX long tail), ModelArk (Seedream), OpenAI.

This module is a thin orchestrator: it resolves the user's model shorthand to a
(model_id, region, api, backend) routing tuple, builds a typed `ImageRequest`, and
hands it to `backend.run_image(...)`. Every backend owns its own body-building,
dispatch, extraction, and dry-run plan rendering — so there is no per-backend
branching here (the Open/Closed seam).

Vertex paths (default, no API key needed):
  - Gemini image ("nano-banana") via :generateContent — supports --ref.
  - Imagen via :predict — text-to-image only (no --ref).
fal path (opt-in, FAL_KEY): FLUX t2i/i2i + the source-image modify ops.
ModelArk path (opt-in, ARK_API_KEY): Seedream native multi-reference i2i.
OpenAI path (opt-in, OPENAI_API_KEY): gpt-image-2 t2i + reference edits.
"""

from __future__ import annotations

from pathlib import Path

from nazca.backends import get_backend

# Re-exported for back-compat with callers/tests that reach for these names here.
from nazca.backends.modelark import (  # noqa: F401
    _SEEDREAM_EDGE,
    _SEEDREAM_MAX_PX,
    _SEEDREAM_MAX_REFS,
    _SEEDREAM_MIN_PX,
    _seedream_body,
    _seedream_size,
)
from nazca.backends.openai import OPENAI_ASPECT_MAP as _OPENAI_ASPECT_MAP
from nazca.backends.vertex import VertexBackend as _VertexBackend
from nazca.cost import cost_from_openai_usage, estimate_image_cost
from nazca.errors import ImageError  # noqa: F401  (re-exported for back-compat)
from nazca.media import encode_image_b64  # noqa: F401  (re-export for vertex_batch)
from nazca.models import MODELS as _MODEL_REGISTRY
from nazca.request import ImageRequest
from nazca.resolve import resolve as _resolve_unified

# Re-export the Gemini extractor for nazca.vertex_batch (batch decode path).
_gemini_extract = _VertexBackend._gemini_extract


# shorthand -> (model id, location/fal-id, api, backend)
# Derived from the canonical registry in nazca.models — do not edit values here;
# edit nazca/models.py instead.
MODELS: dict[str, tuple[str, str, str, str]] = {
    sh: (spec.provider_id, spec.region, spec.api, spec.backend)
    for sh, spec in _MODEL_REGISTRY.items()
}

DEFAULT_MODEL = "nano-banana"

# Source-image modify ops and their default models.
MODIFY_OPS = ("upscale", "bg_remove", "inpaint", "outpaint")
_MODIFY_DEFAULT_MODEL = {
    "upscale": "upscale",
    "bg_remove": "rmbg",
    "inpaint": "inpaint",
    "outpaint": "outpaint",
}

# tier tags: each shorthand → "cheap" | "premium"
# Derived from the canonical registry in nazca.models.
MODEL_TIERS: dict[str, str] = {
    sh: spec.tier
    for sh, spec in _MODEL_REGISTRY.items()
}

# tier → default Vertex-direct model (never auto-route to fal)
_TIER_DEFAULTS: dict[str, str] = {
    "cheap":   "nano-banana",
    "premium": "nano-banana-pro",
}


def select_model(tier: str | None) -> str | None:
    """Return the default model shorthand for *tier*, or None if tier is None."""
    if tier is None:
        return None
    return _TIER_DEFAULTS.get(tier)


def _resolve(model: str | None) -> tuple[str, str, str, str]:
    rm = _resolve_unified(model, "image")
    return (rm.provider_id, rm.region, rm.api, rm.backend)


def _estimate_image_cost(
    model: str | None,
    backend_name: str,
    *,
    aspect_ratio: str | None,
    size: str | None,
    quality: str | None,
) -> float | None:
    """Approximate USD cost for the dry-run plan, keyed by the user shorthand.

    Raw ids / overrides return None ("cost unknown"). Kept in the orchestrator
    because pricing is keyed by the user-facing shorthand the backend never sees.
    """
    aspect_size = _OPENAI_ASPECT_MAP.get(aspect_ratio or "", "auto") if backend_name == "openai" else None
    est = estimate_image_cost(model or DEFAULT_MODEL, size=size, aspect_size=aspect_size, quality=quality)
    return round(est.usd, 4) if est is not None else None


# ----------------------------------------------------------------------- public
def generate_image(
    out: str | Path,
    prompt: str,
    *,
    ref: str | Path | list[str | Path] | None = None,
    model: str | None = None,
    aspect_ratio: str | None = "9:16",
    size: str | None = "2K",
    quality: str | None = None,
    output_format: str | None = None,
    transparent: bool = False,
    op: str | None = None,
    dry_run: bool = False,
) -> Path | dict:
    """Generate (or restyle, when ref is given) one image.

    Vertex/Gemini: supports --ref (one or many; gemini-3-pro-image up to 14)
      and `size` 1K/2K/4K (gemini-3 only).
    Vertex/Imagen: text-to-image only, rejects --ref.
    fal/FLUX: text-to-image; --ref sends the first image as a data-URI.
    OpenAI/gpt-image-2: t2i + edits; `quality` (low|medium|high|auto) sets the
      cost/speed lever. `output_format` (png/jpeg/webp) and `transparent` (bool)
      are gpt-image-2 only. Ignored by other backends.

    Returns the output path; dry_run returns the plan dict (no API call, no key needed).
    """
    out = Path(out)
    model_id, region, api, backend_name = _resolve(model)
    backend = get_backend(backend_name)

    if ref is None:
        refs: list[str] = []
    elif isinstance(ref, (list, tuple)):
        refs = [str(r) for r in ref]
    else:
        refs = [str(ref)]

    req = ImageRequest(
        prompt=prompt,
        refs=refs,
        aspect_ratio=aspect_ratio,
        size=size,
        quality=quality,
        output_format=output_format,
        transparent=transparent,
        op=op,
        est_cost_usd=_estimate_image_cost(
            model, backend_name, aspect_ratio=aspect_ratio, size=size, quality=quality
        ),
        dry_run=dry_run,
    )

    result = backend.run_image(model_id, api, region, req)
    if dry_run:
        return result
    out.write_bytes(result)
    return out


def image_cost_label(
    model: str | None,
    *,
    aspect_ratio: str | None = None,
    size: str | None = None,
    quality: str | None = None,
) -> str | None:
    """Cost line for a COMPLETED real image run, e.g. "~$0.05" or "$0.04".

    gpt-image-2 is token-billed: if the OpenAI backend captured a `usage` block on
    the last dispatch, report the ACTUAL cost from it (no "~"). Otherwise fall back
    to the size×quality estimate. Flat-priced models report their known per-image
    price. Returns None when we have no pricing (raw ids, fal modify ops).
    """
    _model_id, _location, _api, backend_name = _resolve(model)

    if backend_name == "openai":
        backend = get_backend(backend_name)
        actual = cost_from_openai_usage(getattr(backend, "last_usage", None))
        if actual is not None:
            return actual.label()
        aspect_size = _OPENAI_ASPECT_MAP.get(aspect_ratio or "", "auto")
        est = estimate_image_cost(model or DEFAULT_MODEL, aspect_size=aspect_size, quality=quality)
        return est.label() if est is not None else None

    est = estimate_image_cost(model or DEFAULT_MODEL, size=size)
    return est.label() if est is not None else None


def default_modify_model(op: str) -> str:
    """Default model shorthand for a source-image modify op."""
    return _MODIFY_DEFAULT_MODEL[op]


def modify_image(
    out: str | Path,
    source: str | Path,
    *,
    op: str,
    model: str | None = None,
    prompt: str | None = None,
    mask: str | Path | None = None,
    upscale_factor: int = 2,
    expand: int = 256,
    dry_run: bool = False,
) -> Path | dict:
    """Apply a source-image modify op via fal. Verified fal schemas (2026-06-22):

    upscale   → clarity-upscaler   {image_url, upscale_factor}
    bg_remove → birefnet/v2        {image_url, output_format:"png"}  (transparent PNG)
    inpaint   → flux-pro/v1/fill   {image_url, mask_url, prompt}     (mask: white=edit)
    outpaint  → flux-2-pro/outpaint {image_url, expand_top/bottom/left/right}

    The body is built once and reused for dry-run and real send (only base64
    data-URIs are summarized), so the planned JSON matches what's POSTed.
    Returns the output path; dry_run returns the plan dict.
    """
    out = Path(out)
    resolved = model or _MODIFY_DEFAULT_MODEL[op]
    model_id, region, api, backend_name = _resolve(resolved)
    if backend_name != "fal":
        raise ImageError(f"modify op '{op}' needs a fal model; '{resolved}' resolves to {backend_name}")
    backend = get_backend(backend_name)

    req = ImageRequest(
        prompt=prompt or "",
        op=op,
        source=str(source),
        mask=str(mask) if mask is not None else None,
        upscale_factor=upscale_factor,
        expand=expand,
        dry_run=dry_run,
    )

    result = backend.run_image(model_id, api, region, req)
    if dry_run:
        return result
    out.write_bytes(result)
    return out
