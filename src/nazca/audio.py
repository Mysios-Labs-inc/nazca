"""Audio generation (text-to-speech) — the audio modality entry point.

Mirrors the image/video modules: resolve an audio model shorthand to its backend
+ provider id, hand a single `AudioRequest` to the backend's `run_audio` seam, and
write the result (or the dry-run plan). TTS is billed per 1,000 input characters
(see cost.estimate_audio_cost).
"""

from __future__ import annotations

from pathlib import Path

from nazca.backends import get_backend, require_capability
from nazca.cost import estimate_audio_cost
from nazca.errors import AudioError  # noqa: F401  (re-export for back-compat)
from nazca.media import write_result
from nazca.models import models_for
from nazca.request import AudioRequest

DEFAULT_AUDIO_MODEL = "atlas-tts-grok"

# Mapping of audio model shorthands to provider IDs.
# Derived from the canonical registry in nazca.models.
AUDIO_MODELS: dict[str, str] = {
    sh: spec.provider_id for sh, spec in models_for("audio").items()
}

# tier → default audio model shorthand
_TIER_DEFAULTS: dict[str, str] = {"cheap": "atlas-tts-grok", "premium": "atlas-tts-elevenlabs-v3"}


def select_audio_model(tier: str | None) -> str | None:
    """Return the default audio model shorthand for *tier*, or None."""
    return _TIER_DEFAULTS.get(tier) if tier else None


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
    from nazca.resolve import resolve  # local import: avoids circular at module load

    out = Path(out)
    resolved = resolve(model or DEFAULT_AUDIO_MODEL, "audio")
    backend = require_capability(get_backend(resolved.backend), "audio")

    req = AudioRequest(
        text=text,
        voice=voice,
        output_format=output_format,
        op="tts",
        est_cost_usd=(
            est.usd if (est := estimate_audio_cost(resolved.shorthand, chars=len(text or ""))) else None
        ),
        dry_run=dry_run,
    )

    return write_result(out, backend.run_audio(resolved, req), dry_run)


def audio_cost_label(model: str | None, *, chars: int) -> str | None:
    """Cost line for a TTS synthesis, or None when unpriced."""
    est = estimate_audio_cost(model, chars=chars)
    return est.label() if est else None
