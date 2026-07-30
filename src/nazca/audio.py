"""Audio generation (text-to-speech, plus — issue #122 phases A3/A4 — music and
sound effects) — the audio modality entry point.

Mirrors the image/video modules: resolve an audio model shorthand to its backend
+ provider id, hand a single `AudioRequest` to the backend's `run_audio` seam, and
write the result (or the dry-run plan). TTS is billed per 1,000 input characters;
music/sfx (and any other flat-per-generation audio op) are billed per generation
(see cost.estimate_audio_cost).
"""

from __future__ import annotations

from pathlib import Path

from nazca.backends import get_backend, require_capability
from nazca.cost import estimate_audio_cost
from nazca.errors import AudioError  # noqa: F401  (re-export for back-compat)
from nazca.media import write_result
from nazca.models import AUDIO_PROVIDER_IDS as AUDIO_MODELS  # noqa: F401  (re-export)
from nazca.request import AudioRequest

DEFAULT_AUDIO_MODEL = "atlas-tts-grok"
DEFAULT_MUSIC_MODEL = "atlas-music-minimax"
DEFAULT_SFX_MODEL = "elevenlabs-sfx"

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
    lyrics: str | None = None,
    duration_seconds: float | None = None,
    output_format: str = "mp3",
    op: str = "tts",
    dry_run: bool = False,
) -> Path:
    """Synthesize speech (or, with `op="music"`/`op="sfx"`, a song/sound
    effect) from `text` to `out` (or write the dry-run plan).

    `op` defaults to `"tts"`; `"music"` and `"sfx"` are the other ops wired
    today (issues #122 phases A4/A3 — see `capabilities.AUDIO_OPS` /
    docs/media-modalities.md for the rest of the named-but-unwired
    vocabulary). `lyrics` is music-only (optional `[Verse]`/`[Chorus]`-
    structured text); `duration_seconds` is sfx-only (target length in
    seconds); both are ignored by ops that don't use them. Exposed as real
    parameters, not hardcoded, so future ops can route through the same seam
    once a backend implements them. `op` is validated against the resolved
    model's `Caps` before dispatch — unlike `op` on image/video's modify
    calls, no backend reads `AudioRequest.op` for anything but its own wired
    ops today, so an unmapped op would otherwise silently misbehave (fall
    back to plain TTS on Atlas, or be ignored outright on Worder/Fish)
    instead of erroring.
    """
    from nazca.capabilities import validate_op
    from nazca.resolve import resolve  # local import: avoids circular at module load

    out = Path(out)
    resolved = resolve(model or DEFAULT_AUDIO_MODEL, "audio")
    validate_op(resolved.shorthand, op)
    backend = require_capability(get_backend(resolved.backend), "audio")

    req = AudioRequest(
        text=text,
        voice=voice,
        lyrics=lyrics,
        duration_seconds=duration_seconds,
        output_format=output_format,
        op=op,
        est_cost_usd=(
            est.usd if (est := estimate_audio_cost(resolved.shorthand, chars=len(text or ""))) else None
        ),
        dry_run=dry_run,
    )

    return write_result(out, backend.run_audio(resolved, req), dry_run)


def generate_music(
    out: str | Path,
    prompt: str,
    *,
    model: str | None = None,
    lyrics: str | None = None,
    output_format: str = "mp3",
    dry_run: bool = False,
) -> Path:
    """Generate a song from a style `prompt` (optionally with `lyrics`) to `out`.

    A thin wrapper over `speak(..., op="music")` — same resolve/validate/
    dispatch/write_result seam, just a clearer public name than calling
    `speak()` with an `op` kwarg for something that isn't speech.
    """
    return speak(
        out, prompt, model=model or DEFAULT_MUSIC_MODEL, lyrics=lyrics,
        output_format=output_format, op="music", dry_run=dry_run,
    )


def generate_sfx(
    out: str | Path,
    prompt: str,
    *,
    model: str | None = None,
    duration_seconds: float | None = None,
    output_format: str = "mp3",
    dry_run: bool = False,
) -> Path:
    """Generate a sound effect from a text `prompt` (a sound description, not
    speech) to `out`.

    A thin wrapper over `speak(..., op="sfx")` — same resolve/validate/
    dispatch/write_result seam, just a clearer public name than calling
    `speak()` with an `op` kwarg for something that isn't speech.
    """
    return speak(
        out, prompt, model=model or DEFAULT_SFX_MODEL, duration_seconds=duration_seconds,
        output_format=output_format, op="sfx", dry_run=dry_run,
    )


def audio_cost_label(model: str | None, *, chars: int = 0) -> str | None:
    """Cost line for an audio generation (TTS: per-char; music: flat), or None
    when unpriced. `chars` is ignored for flat-rate ops like music.
    """
    est = estimate_audio_cost(model, chars=chars)
    return est.label() if est else None
