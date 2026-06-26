"""Audio generation (text-to-speech) — the audio modality entry point.

Mirrors the image/video modules: resolve an audio model shorthand to its backend
+ provider id, hand a single `AudioRequest` to the backend's `run_audio` seam, and
write the result (or the dry-run plan). TTS is billed per 1,000 input characters
(see cost.estimate_audio_cost).
"""

from __future__ import annotations

from pathlib import Path

from nazca.backends import get_backend
from nazca.cost import estimate_audio_cost
from nazca.errors import AudioError
from nazca.media import write_result
from nazca.models import AUDIO_MODELS as _AUDIO_REGISTRY
from nazca.request import AudioRequest

DEFAULT_AUDIO_MODEL = "atlas-tts-grok"

# audio shorthand → provider id (derived from the canonical registry)
AUDIO_MODELS: dict[str, str] = {sh: spec.provider_id for sh, spec in _AUDIO_REGISTRY.items()}

# tier → default audio model shorthand
_TIER_DEFAULTS: dict[str, str] = {"cheap": "atlas-tts-grok", "premium": "atlas-tts-elevenlabs-v3"}


def select_audio_model(tier: str | None) -> str | None:
    """Return the default audio model shorthand for *tier*, or None."""
    return _TIER_DEFAULTS.get(tier) if tier else None


def _resolve_audio(model: str | None) -> tuple[str, str]:
    """Resolve an audio model shorthand to (backend_name, provider_id)."""
    model = model or DEFAULT_AUDIO_MODEL
    if ":" in model:  # backend:rawid passthrough
        prefix, raw_id = model.split(":", 1)
        if prefix.lower() == "atlas":
            return ("atlas", raw_id)
    spec = _AUDIO_REGISTRY.get(model)
    if spec is None:
        raise AudioError(
            f"unknown audio model '{model}' (have: {', '.join(_AUDIO_REGISTRY)})"
        )
    return (spec.backend, spec.provider_id)


def speak(
    out: str | Path,
    text: str,
    *,
    model: str | None = None,
    voice: str | None = None,
    output_format: str = "mp3",
    dry_run: bool = False,
) -> Path:
    """Synthesize speech from `text` to `out` (or write the dry-run plan)."""
    out = Path(out)
    resolved = model or DEFAULT_AUDIO_MODEL
    backend_name, provider_id = _resolve_audio(resolved)
    backend = get_backend(backend_name)

    req = AudioRequest(
        text=text,
        voice=voice,
        output_format=output_format,
        op="tts",
        est_cost_usd=(
            est.usd if (est := estimate_audio_cost(resolved, chars=len(text or ""))) else None
        ),
        dry_run=dry_run,
    )

    return write_result(out, backend.run_audio(provider_id, req), dry_run)


def audio_cost_label(model: str | None, *, chars: int) -> str | None:
    """Cost line for a TTS synthesis, or None when unpriced."""
    est = estimate_audio_cost(model, chars=chars)
    return est.label() if est else None
