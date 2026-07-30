"""Voice creation/conversion (`voice_clone`/`voice_design`/`speech_to_speech`)
— issue #122 phases A2/A3.

Mirrors `audio.py`'s style (module docstring, resolve+backend pattern) but
these functions don't fit `speak()`'s text->single-file shape: `clone_voice`
uploads audio samples and returns model metadata (no media file is produced);
`design_voice` returns several audio *candidates* in one response instead of
one output file; `speech_to_speech` takes a local audio FILE as its primary
input instead of text, converting it into a target voice's speech. So they
live in their own module rather than being bolted onto `audio.speak()`/
`AudioRequest`. Fish Audio (`fish-voice-clone`/`fish-voice-design`, phase A2)
and ElevenLabs (`elevenlabs-voice-clone`/`elevenlabs-voice-design`/
`elevenlabs-speech-to-speech`, phase A3) dispatch through these functions —
the backend Protocol (`SupportsVoiceClone`/`SupportsVoiceDesign`/
`SupportsSpeechToSpeech`) is the only contract, so adding a backend needs no
changes here.

`visibility`/`tags` (`clone_voice`) and `n`/`language`/`speed`
(`design_voice`) are Fish-specific knobs accepted by every backend's call
signature (per the shared Protocol) but not honored by backends that lack an
equivalent (currently ElevenLabs) — passing a non-default value to a backend
that can't honor it raises an `AudioError` rather than silently discarding
it; see each backend's own docstring for exactly which fields it honors.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

from nazca.backends import get_backend
from nazca.backends.base import require_capability
from nazca.errors import AudioError

DEFAULT_VOICE_CLONE_MODEL = "fish-voice-clone"
DEFAULT_VOICE_DESIGN_MODEL = "fish-voice-design"
DEFAULT_SPEECH_TO_SPEECH_MODEL = "elevenlabs-speech-to-speech"

# response key each voice_clone-capable backend uses to identify the created
# voice — checked in this order since "_id" (Fish) won't collide with
# "voice_id" (ElevenLabs). Extend this tuple, not clone_voice()'s body, when a
# new backend uses yet another key.
_VOICE_ID_KEYS = ("_id", "voice_id")


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
    produced); the id field name is backend-specific (Fish: `_id`, ElevenLabs:
    `voice_id`) — use `result["voice_id"]` for `nazca speak --voice <id>`,
    added below alongside the backend's own key so callers don't need to know
    which backend they hit.

    `visibility`/`tags` are silently dropped by backends that don't support
    them (currently ElevenLabs) — if either is non-default and the resolved
    backend isn't Fish (the only one that honors them today), this raises an
    `AudioError` rather than pretending the values took effect.
    """
    from nazca.capabilities import validate_op
    from nazca.resolve import resolve  # local import: avoids circular at module load

    resolved = resolve(model or DEFAULT_VOICE_CLONE_MODEL, "audio")
    validate_op(resolved.shorthand, "voice_clone")
    backend = require_capability(get_backend(resolved.backend), "voice_clone")

    if resolved.backend != "fish" and (visibility != "private" or tags):
        raise AudioError(
            f"--visibility/--tags aren't supported by {resolved.shorthand!r} "
            "(Fish-only) — omit them for this backend."
        )

    result = backend.voice_clone(
        title,
        audio_paths,
        description=description,
        visibility=visibility,
        tags=tags,
        dry_run=dry_run,
    )
    if dry_run:
        return result

    for key in _VOICE_ID_KEYS:
        if key in result:
            result.setdefault("voice_id", result[key])
            return result
    raise AudioError(
        f"{resolved.shorthand} voice_clone response is missing an id field "
        f"(expected one of {_VOICE_ID_KEYS}): {result!r}"
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

    `n`/`language`/`speed` are real Fish body fields but have no ElevenLabs
    equivalent — if any is non-default and the resolved backend isn't Fish,
    this raises an `AudioError` rather than silently returning however many
    previews ElevenLabs' model happens to produce while the caller believes
    their requested count/language/speed took effect.
    """
    from nazca.capabilities import validate_op
    from nazca.resolve import resolve  # local import: avoids circular at module load

    resolved = resolve(model or DEFAULT_VOICE_DESIGN_MODEL, "audio")
    validate_op(resolved.shorthand, "voice_design")
    backend = require_capability(get_backend(resolved.backend), "voice_design")

    if resolved.backend != "fish" and (n != 2 or language is not None or speed != 1.0):
        raise AudioError(
            f"--language/-n/--speed aren't supported by {resolved.shorthand!r} "
            "(Fish-only) — omit them for this backend."
        )

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

    if "candidates" not in result:
        raise AudioError(
            f"{resolved.shorthand} voice-design response is missing 'candidates': {result!r}"
        )

    candidates = []
    for c in result["candidates"]:
        candidate = dict(c)
        b64 = candidate.pop("audio_base64", None)
        if not b64:
            raise AudioError(
                f"{resolved.shorthand} voice-design candidate {candidate.get('id', '?')!r} "
                "has no audio_base64 — refusing to write an empty/corrupt audio file"
            )
        try:
            candidate["audio_bytes"] = base64.b64decode(b64, validate=True)
        except binascii.Error as e:
            raise AudioError(
                f"{resolved.shorthand} voice-design candidate {candidate.get('id', '?')!r} "
                f"has malformed audio_base64: {e}"
            ) from e
        candidates.append(candidate)
    return {"candidates": candidates}


def speech_to_speech(
    source_audio_path: str,
    *,
    out: str,
    voice: str,
    model: str | None = None,
    output_format: str = "mp3",
    dry_run: bool = False,
) -> object:
    """Convert `source_audio_path`'s speech into `voice`'s voice (the "voice
    changer" op, issue #122 phase A3) and write the result to `out` (or the
    dry-run plan sidecar via `media.write_result`, same convention as
    `audio.speak`/`threed.make_3d`).

    Mirrors `clone_voice`'s resolve -> validate_op -> require_capability ->
    dispatch structure closely, but the return value is a `Path` (a media
    file), not a metadata `dict` — `speech_to_speech` produces a genuine audio
    output, unlike `clone_voice`/`design_voice`, so it goes through
    `media.write_result` the same way `audio.speak`/`threed.make_3d` do.
    """
    from nazca.capabilities import validate_op
    from nazca.cost import estimate_audio_cost
    from nazca.media import write_result
    from nazca.request import SpeechToSpeechRequest
    from nazca.resolve import resolve  # local import: avoids circular at module load

    out_path = Path(out)
    resolved = resolve(model or DEFAULT_SPEECH_TO_SPEECH_MODEL, "audio")
    validate_op(resolved.shorthand, "speech_to_speech")
    backend = require_capability(get_backend(resolved.backend), "speech_to_speech")

    req = SpeechToSpeechRequest(
        source_audio_path=source_audio_path,
        voice=voice,
        output_format=output_format,
        est_cost_usd=(
            est.usd if (est := estimate_audio_cost(resolved.shorthand)) else None
        ),
        dry_run=dry_run,
    )

    return write_result(out_path, backend.speech_to_speech(resolved, req), dry_run)
