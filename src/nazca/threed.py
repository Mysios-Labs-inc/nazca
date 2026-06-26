"""3D generation (text/image → GLB mesh) — the 3D modality entry point.

Mirrors the image/video/audio modules: resolve a 3D model shorthand to its backend
+ provider id, hand a single `ThreeDRequest` to the backend's `run_3d` seam, and
write the GLB result (or the dry-run plan). 3D is billed per run.
"""

from __future__ import annotations

from pathlib import Path

from nazca.backends import get_backend
from nazca.cost import estimate_3d_cost
from nazca.errors import BackendError
from nazca.media import write_result
from nazca.models import THREED_MODELS as _THREED_REGISTRY
from nazca.request import ThreeDRequest

DEFAULT_3D_MODEL = "atlas-hunyuan3d-rapid"
_TIER_DEFAULTS: dict[str, str] = {"cheap": "atlas-hunyuan3d-rapid", "premium": "atlas-hunyuan3d-pro"}


class ThreeDError(BackendError):
    """Raised when 3D generation fails or no 3D model is resolvable."""


def select_3d_model(tier: str | None) -> str | None:
    """Return the default 3D model shorthand for *tier*, or None."""
    return _TIER_DEFAULTS.get(tier) if tier else None


def _resolve_3d(model: str | None) -> tuple[str, str]:
    """Resolve a 3D model shorthand to (backend_name, provider_id)."""
    model = model or DEFAULT_3D_MODEL
    if ":" in model:  # backend:rawid passthrough
        prefix, raw_id = model.split(":", 1)
        if prefix.lower() == "atlas":
            return ("atlas", raw_id)
    spec = _THREED_REGISTRY.get(model)
    if spec is None:
        raise ThreeDError(f"unknown 3D model '{model}' (have: {', '.join(_THREED_REGISTRY)})")
    return (spec.backend, spec.provider_id)


def make_3d(
    out: str | Path,
    prompt: str = "",
    *,
    source: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
) -> Path:
    """Generate a 3D asset (GLB) from text (`prompt`) or an image (`source`)."""
    out = Path(out)
    resolved = model or DEFAULT_3D_MODEL
    backend_name, provider_id = _resolve_3d(resolved)
    backend = get_backend(backend_name)

    op = "i23d" if source else "t23d"
    est = estimate_3d_cost(resolved)
    req = ThreeDRequest(
        prompt=prompt,
        source=str(source) if source else None,
        op=op,
        est_cost_usd=est.usd if est else None,
        dry_run=dry_run,
    )

    return write_result(out, backend.run_3d(provider_id, req), dry_run)


def threed_cost_label(model: str | None) -> str | None:
    """Cost line for a 3D generation, or None when unpriced."""
    est = estimate_3d_cost(model)
    return est.label() if est else None
