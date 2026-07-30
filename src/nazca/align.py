"""Forced alignment (audio + text -> timed transcript) — issue #122 phase A3's
final sub-phase, after `tts`/`sfx`/`voice_clone`/`voice_design`/
`speech_to_speech`/`stt`.

Mirrors `voice.py`'s shape more than `audio.py`'s: `align`'s input isn't
text-in/audio-out (that's `AudioRequest`/`speak()`), it's a LOCAL source audio
file *plus* a text transcript in, JSON timing data out — closer to
`voice_clone`'s "upload a file, get back structured metadata" shape than to
`speak()`'s "write one media file". So, like `voice.py`, this lives in its own
module rather than being bolted onto `audio.py`.

Unlike `voice.py`'s `clone_voice`/`design_voice` (which return the raw dict to
the caller), `align_audio` writes the result via `media.write_result` — same
resolve/validate/dispatch/write seam as `audio.py`/`threed.py` — since the
CLI command takes an `-o` output path (a `nazca align SOURCE --text "..." -o
alignment.json` sibling of `nazca make3d ... -o out.glb`), not a bare return
value like `voice-clone`/`voice-design`.
"""

from __future__ import annotations

from pathlib import Path

from nazca.backends import get_backend, require_capability
from nazca.media import write_result

DEFAULT_ALIGN_MODEL = "elevenlabs-align"


def align_audio(
    out: str | Path,
    source: str,
    text: str,
    *,
    model: str | None = None,
    dry_run: bool = False,
) -> Path:
    """Force-align `text` to the audio file `source`, writing the timing JSON
    to `out` (or the dry-run plan).

    `source` is a LOCAL file path (unlike Fish/Worder/ElevenLabs TTS's `--voice`,
    which names a remote resource) — the backend reads it directly, there is no
    URL form.
    """
    from nazca.capabilities import validate_op
    from nazca.cost import estimate_audio_cost
    from nazca.resolve import resolve  # local import: avoids circular at module load

    out = Path(out)
    resolved = resolve(model or DEFAULT_ALIGN_MODEL, "audio")
    validate_op(resolved.shorthand, "align")
    backend = require_capability(get_backend(resolved.backend), "align")

    est_cost_usd = est.usd if (est := estimate_audio_cost(resolved.shorthand)) else None
    result = backend.align(source, text, est_cost_usd=est_cost_usd, dry_run=dry_run)
    return write_result(out, result, dry_run)
