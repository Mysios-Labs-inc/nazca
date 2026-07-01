"""Unified model resolution — one entry point for every modality.

`resolve(model, modality)` collapses the four hand-rolled resolvers
(`image._resolve`, `video._resolve_video`, `audio._resolve_audio`,
`threed._resolve_3d`) into a single function that returns a typed
:class:`ResolvedModel`. It reproduces each old resolver's behavior EXACTLY —
the same backend:rawid prefix table, the same user-override lookup, the same
built-in registry lookup order, and the same fallback / raise-on-unknown
posture — so callers can be migrated one at a time with zero behavior drift.

Parity map (old tuple → ResolvedModel fields):
  image  : (provider_id, region, api, backend) → (provider_id, region, api, backend)
  video  : (backend, provider_id)              → backend, provider_id; region/api from
                                                  spec when one exists (e.g. Vertex
                                                  veo/omni), else ""
  audio  : (backend, provider_id)              → backend, provider_id; region="", api=""
  3d     : (backend, provider_id)              → backend, provider_id; region="", api=""

`spec` carries the canonical :class:`~nazca.models.ModelSpec` when the model
resolves through a built-in registry; it is ``None`` for prefix-passthrough,
raw-id fallback, and user-override results (none of which have a registry spec).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nazca import config, registry
from nazca.models import (
    ARK_VIDEO_MODELS as _ARK_VIDEO_MODELS,
)
from nazca.models import (
    ATLAS_VIDEO_MODELS as _ATLAS_VIDEO_MODELS,
)
from nazca.models import (
    AUDIO_MODELS as _AUDIO_REGISTRY,
)
from nazca.models import (
    FAL_VIDEO_MODELS as _FAL_VIDEO_MODELS,
)
from nazca.models import (
    MODELS as _MODEL_REGISTRY,
)
from nazca.models import (
    THREED_MODELS as _THREED_REGISTRY,
)
from nazca.models import (
    VEO_ALIASES as _VEO_ALIASES,
)
from nazca.models import (
    VIDEO_MODELS as _VIDEO_REGISTRY,
)
from nazca.models import (
    ModelSpec,
)

Modality = Literal["image", "video", "audio", "3d"]

# Per-modality defaults, mirroring the old resolvers/orchestrators exactly.
#   image : image._resolve            → DEFAULT_MODEL "nano-banana"
#   video : video.generate_video      → `model or config.VEO_MODEL`
#   audio : audio._resolve_audio      → DEFAULT_AUDIO_MODEL "atlas-tts-grok"
#   3d    : threed._resolve_3d        → DEFAULT_3D_MODEL "atlas-hunyuan3d-rapid"
_DEFAULT_IMAGE_MODEL = "nano-banana"
_DEFAULT_AUDIO_MODEL = "atlas-tts-grok"
_DEFAULT_3D_MODEL = "atlas-hunyuan3d-rapid"


@dataclass(frozen=True)
class ResolvedModel:
    """A fully-resolved routing target for a single generation request.

    shorthand   the (defaulted) user-facing model string that was resolved.
    provider_id the raw upstream model id handed to the backend.
    backend     dispatch key: "vertex" | "fal" | "modelark" | "openai" | "atlas".
    api         sub-routing within the backend ("gemini"/"imagen"/"fal"/... for
                image; "veo"/"omni" for Vertex video when spec carries one, else
                "" for audio/3d — matching the old 2-tuples).
    region      provider region (vertex image/video when spec carries one; ""
                everywhere else).
    spec        canonical ModelSpec when resolved via a built-in registry, else
                None (prefix-passthrough / raw-id fallback / user override).
    """

    shorthand: str
    provider_id: str
    backend: str
    api: str
    region: str
    spec: ModelSpec | None


# --------------------------------------------------------------------------- #
# Shared backend:rawid prefix table.
#
# Maps a lowercased prefix to its backend dispatch key. region/api are NOT stored
# here because they are image-specific (video/audio/3d always use ""); image fills
# them from `_IMAGE_PREFIX_RA` keyed by backend. Per-modality `allowed` sets gate
# which prefixes are honored (e.g. audio/3d only honor "atlas"; video drops
# "openai"/"oai"), reproducing each old resolver's prefix branch exactly.
# --------------------------------------------------------------------------- #
_PREFIX_BACKEND: dict[str, str] = {
    "vertex": "vertex",
    "veo": "vertex",
    "fal": "fal",
    "ark": "modelark",
    "modelark": "modelark",
    "openai": "openai",
    "oai": "openai",
    "atlas": "atlas",
}

# Image-only (region, api) per resolved backend — from image._resolve's prefix arm.
_IMAGE_PREFIX_RA: dict[str, tuple[str, str]] = {
    "vertex": ("us-central1", "gemini"),
    "fal": ("", "fal"),
    "modelark": ("", "modelark"),
    "openai": ("", "openai"),
    "atlas": ("", "atlas"),
}

_IMAGE_PREFIXES = frozenset(_PREFIX_BACKEND)  # all prefixes
_VIDEO_PREFIXES = frozenset({"vertex", "veo", "fal", "ark", "modelark", "atlas"})
_AUDIO_PREFIXES = frozenset({"atlas"})
_3D_PREFIXES = frozenset({"atlas"})


def _match_prefix(model: str, allowed: frozenset[str]) -> tuple[str, str] | None:
    """Shared prefix splitter. Returns ``(backend, raw_id)`` when *model* is a
    ``backend:rawid`` string whose prefix is in *allowed*, else ``None``."""
    if ":" not in model:
        return None
    prefix, raw_id = model.split(":", 1)
    prefix = prefix.lower()
    if prefix not in allowed:
        return None
    return _PREFIX_BACKEND[prefix], raw_id


# Video projection tables (VEO_ALIASES / FAL_/ARK_/ATLAS_VIDEO_MODELS) are owned by
# nazca.models and imported above — one canonical home for every registry-derived view.


# --------------------------------------------------------------------------- #
# Per-modality resolvers (each a faithful copy of the corresponding old _resolve*).
# --------------------------------------------------------------------------- #
def _resolve_image(model: str) -> ResolvedModel:
    # 1. backend:rawid prefix passthrough
    pm = _match_prefix(model, _IMAGE_PREFIXES)
    if pm is not None:
        backend, raw_id = pm
        region, api = _IMAGE_PREFIX_RA[backend]
        return ResolvedModel(model, raw_id, backend, api, region, None)

    # 2. user override file (~/.config/nazca/models.json)
    ov = registry.image_override(model)
    if ov is not None:
        return ResolvedModel(
            model,
            ov.get("id", model),
            ov.get("backend", "vertex"),
            ov.get("api", "gemini"),
            ov.get("region", "us-central1"),
            None,
        )

    # 3. built-in registry
    spec = _MODEL_REGISTRY.get(model)
    if spec is not None:
        return ResolvedModel(model, spec.provider_id, spec.backend, spec.api, spec.region, spec)

    # 4. fallback: raw vertex id → Gemini family, default region, vertex backend
    return ResolvedModel(model, model, "vertex", "gemini", "us-central1", None)


def _resolve_video(model: str) -> ResolvedModel:
    # 1. backend:rawid prefix passthrough
    pm = _match_prefix(model, _VIDEO_PREFIXES)
    if pm is not None:
        backend, raw_id = pm
        return ResolvedModel(model, raw_id, backend, "", "", None)

    # 2. user override file
    ov = registry.video_override(model)
    if ov is not None:
        ov_backend = ov.get("backend", "vertex")
        ov_id = ov.get("id", model)
        if ov_backend in ("fal", "modelark", "atlas"):
            return ResolvedModel(model, ov_id, ov_backend, "", "", None)
        return ResolvedModel(model, ov_id, "vertex", "", "", None)  # vertex: raw Veo id

    # 3. built-in registries (fal → ModelArk → Atlas → Vertex aliases)
    if model in _FAL_VIDEO_MODELS:
        return ResolvedModel(model, _FAL_VIDEO_MODELS[model], "fal", "", "", _VIDEO_REGISTRY[model])
    if model in _ARK_VIDEO_MODELS:
        return ResolvedModel(
            model, _ARK_VIDEO_MODELS[model], "modelark", "", "", _VIDEO_REGISTRY[model]
        )
    if model in _ATLAS_VIDEO_MODELS:
        return ResolvedModel(
            model, _ATLAS_VIDEO_MODELS[model], "atlas", "", "", _VIDEO_REGISTRY[model]
        )
    # Vertex alias hit carries a spec; raw-id fallback does not. api/region come
    # from the spec (e.g. omni-flash needs api="omni", region="global" to route
    # off the Veo predictLongRunning path) — "" only for the raw-id fallback.
    spec = _VIDEO_REGISTRY.get(model) if model in _VEO_ALIASES else None
    api = spec.api if spec is not None else ""
    region = spec.region if spec is not None else ""
    return ResolvedModel(model, _VEO_ALIASES.get(model, model), "vertex", api, region, spec)


def _resolve_audio(model: str) -> ResolvedModel:
    pm = _match_prefix(model, _AUDIO_PREFIXES)
    if pm is not None:
        backend, raw_id = pm
        return ResolvedModel(model, raw_id, backend, "", "", None)
    spec = _AUDIO_REGISTRY.get(model)
    if spec is None:
        from nazca.audio import AudioError  # lazy: avoid orchestrator import cycle

        raise AudioError(f"unknown audio model '{model}' (have: {', '.join(_AUDIO_REGISTRY)})")
    return ResolvedModel(model, spec.provider_id, spec.backend, "", "", spec)


def _resolve_3d(model: str) -> ResolvedModel:
    pm = _match_prefix(model, _3D_PREFIXES)
    if pm is not None:
        backend, raw_id = pm
        return ResolvedModel(model, raw_id, backend, "", "", None)
    spec = _THREED_REGISTRY.get(model)
    if spec is None:
        from nazca.threed import ThreeDError  # lazy: avoid orchestrator import cycle

        raise ThreeDError(f"unknown 3D model '{model}' (have: {', '.join(_THREED_REGISTRY)})")
    return ResolvedModel(model, spec.provider_id, spec.backend, "", "", spec)


def resolve(model: str | None, modality: Modality) -> ResolvedModel:
    """Resolve *model* for *modality* into a typed :class:`ResolvedModel`.

    Applies the modality's default when *model* is None, then dispatches to the
    matching resolver. Behavior is byte-for-byte equivalent to the legacy
    ``_resolve``/``_resolve_video``/``_resolve_audio``/``_resolve_3d`` tuples,
    including the unknown-model fallback (image/video) and the raised
    ``AudioError``/``ThreeDError`` (audio/3d).

    The ``model is None`` defaulting here is belt-and-suspenders: every public
    caller already pre-defaults (``generate_video`` -> ``model or config.VEO_MODEL``,
    ``speak``/``make_3d`` likewise), so audio/3d never reach this with None in
    practice — the default just makes ``resolve()`` total rather than raising.
    """
    if modality == "image":
        return _resolve_image(model or _DEFAULT_IMAGE_MODEL)
    if modality == "video":
        return _resolve_video(model or config.VEO_MODEL)
    if modality == "audio":
        return _resolve_audio(model or _DEFAULT_AUDIO_MODEL)
    if modality == "3d":
        return _resolve_3d(model or _DEFAULT_3D_MODEL)
    raise ValueError(f"unknown modality {modality!r}")
