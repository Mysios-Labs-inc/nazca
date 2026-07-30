"""Voice creation (Fish Audio `voice_clone`/`voice_design`) — issue #122 phase A2.

Mirrors `audio.py`'s style (module docstring, resolve+backend pattern) but these
two functions don't fit `speak()`'s text->single-file shape: `clone_voice`
uploads audio samples and returns model metadata (no media file is produced),
and `design_voice` returns several audio *candidates* in one response instead
of one output file. So they live in their own module rather than being bolted
onto `audio.speak()`/`AudioRequest`.
"""

from __future__ import annotations

import base64

from nazca.backends import get_backend
from nazca.errors import AudioError  # noqa: F401  (re-export for back-compat)

DEFAULT_VOICE_CLONE_MODEL = "fish-voice-clone"
DEFAULT_VOICE_DESIGN_MODEL = "fish-voice-design"


def clone_voice(
    title: str,
    audio_paths: list[str],
    *,
    model: str | None = None,
    description: str | None = None,
    visibility: str = "private",
    tags: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Create a reusable voice from audio samples.

    Returns the created model's metadata dict (not a Path — no media file is
    produced); the caller uses the `_id` field as `nazca speak --voice <_id>`.
    """
    from nazca.capabilities import validate_op
    from nazca.resolve import resolve  # local import: avoids circular at module load

    resolved = resolve(model or DEFAULT_VOICE_CLONE_MODEL, "audio")
    validate_op(resolved.shorthand, "voice_clone")
    backend = get_backend(resolved.backend)
    if not hasattr(backend, "voice_clone"):
        raise AudioError(f"backend '{backend.name}' does not support voice_clone")

    return backend.voice_clone(
        title,
        audio_paths,
        description=description,
        visibility=visibility,
        tags=tags,
        dry_run=dry_run,
    )


def design_voice(
    instruction: str,
    *,
    model: str | None = None,
    reference_text: str | None = None,
    language: str | None = None,
    n: int = 2,
    speed: float = 1.0,
    dry_run: bool = False,
) -> dict:
    """Generate `n` candidate voices from a text description.

    Returns `{"candidates": [...]}` — each candidate carries the raw decoded
    audio bytes under `audio_bytes` (decoded from the response's `audio_base64`)
    plus `id`/`index`/`text`/etc, so callers don't need to handle base64
    themselves. On `dry_run=True`, no network call is made and the raw plan
    dict from the backend is returned unchanged (no `candidates` key).
    """
    from nazca.capabilities import validate_op
    from nazca.resolve import resolve  # local import: avoids circular at module load

    resolved = resolve(model or DEFAULT_VOICE_DESIGN_MODEL, "audio")
    validate_op(resolved.shorthand, "voice_design")
    backend = get_backend(resolved.backend)
    if not hasattr(backend, "voice_design"):
        raise AudioError(f"backend '{backend.name}' does not support voice_design")

    result = backend.voice_design(
        instruction,
        reference_text=reference_text,
        language=language,
        n=n,
        speed=speed,
        dry_run=dry_run,
    )
    if dry_run:
        return result

    candidates = []
    for c in result.get("candidates", []):
        candidate = dict(c)
        b64 = candidate.pop("audio_base64", None)
        candidate["audio_bytes"] = base64.b64decode(b64) if b64 else b""
        candidates.append(candidate)
    return {"candidates": candidates}
