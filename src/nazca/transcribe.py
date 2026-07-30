"""Speech-to-text transcription (ElevenLabs `stt`, issue #122 phase A3) — the
first non-audio-*output* op in the audio modality: a local audio file goes
*in*, text/JSON comes *out* (word-level timestamps, detected language, the
plain transcript). Mirrors the resolve → validate_op → dispatch → write_result
seam `audio.py`/`voice.py` already use, but doesn't fit either: `AudioRequest`
is text-in/audio-out (the opposite direction), and `voice.py`'s
`clone_voice`/`design_voice` are audio-in/audio-*and*-metadata-out. So `stt`
gets its own request dataclass (`request.TranscriptionRequest`) and its own
tiny orchestrator here — the same "different shape -> own module" call
`voice.py` made for phase A2.
"""

from __future__ import annotations

from pathlib import Path

from nazca.backends import get_backend, require_capability
from nazca.media import write_result
from nazca.request import TranscriptionRequest

DEFAULT_STT_MODEL = "elevenlabs-stt"


def transcribe(
    out: str | Path,
    source: str | Path,
    *,
    model: str | None = None,
    language: str | None = None,
    dry_run: bool = False,
) -> Path:
    """Transcribe the audio file at `source` and write the result to `out`.

    On a real run, `out` receives the **full decoded JSON response** (e.g.
    ElevenLabs' `{"text", "words": [{"text","start","end","type",...}],
    "language_code", "language_probability", ...}`), not just the plain
    transcript string — word-level timestamps, per-word type (word/spacing/
    audio_event), and the detected language would otherwise be silently
    thrown away. A caller who only wants the plain transcript can pull `.text`
    back out of the written JSON (e.g. `jq -r .text out.json`); this mirrors
    `voice.design_voice`'s "don't guess a narrower shape than the API gives
    you" posture. `-o out.json` is the documented convention (see `nazca
    transcribe --help`); nothing stops a caller from passing a `.txt` path —
    `write_result` writes valid JSON either way, it just isn't shaped like
    plain text.

    On `dry_run=True`, no network call is made — see `write_result` for the
    `<out>.request.json` sidecar convention, same as every other modality.
    """
    from nazca.capabilities import validate_op
    from nazca.resolve import resolve  # local import: avoids circular at module load

    out = Path(out)
    source = Path(source)
    resolved = resolve(model or DEFAULT_STT_MODEL, "audio")
    validate_op(resolved.shorthand, "stt")
    backend = require_capability(get_backend(resolved.backend), "stt")

    req = TranscriptionRequest(
        source_audio_path=str(source),
        language=language,
        dry_run=dry_run,
    )

    return write_result(out, backend.run_stt(resolved, req), dry_run)
