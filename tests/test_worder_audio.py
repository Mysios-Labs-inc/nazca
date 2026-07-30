"""Worder TTS (audio modality) — dry-run plan, missing-voice error, cost, error hints."""

from __future__ import annotations

import json

from click.testing import CliRunner

from nazca.audio import speak
from nazca.backends.error_hints import hint
from nazca.backends.worder import WorderError
from nazca.cli import cli
from nazca.cost import estimate_audio_cost


def test_speak_dry_run_plan_requires_voice(tmp_path):
    out = tmp_path / "hi.mp3"
    plan_path = speak(out, "Hello world", model="worder-tts", voice="voice_abc123", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["backend"] == "worder"
    assert plan["url"] == "https://worder.com/api/v1/generate"
    assert plan["body"] == {"voice_id": "voice_abc123", "text": "Hello world"}


def test_speak_without_voice_raises():
    # Note: WorderError is a BackendError, not an AudioError, mirroring AtlasError —
    # the audio orchestrator only wraps the missing-model case, not backend-specific
    # dispatch failures.
    try:
        speak("/tmp/wont-be-written.mp3", "hi", model="worder-tts", dry_run=True)
    except WorderError as e:
        assert "voice" in str(e).lower()
    else:
        raise AssertionError("expected WorderError when no --voice is given")


def test_audio_cost_unpriced():
    # Worder is per-second, priced per voice actor — not in the flat-rate table.
    assert estimate_audio_cost("worder-tts", chars=1000) is None


def test_cli_speak_worder_dry_run(tmp_path):
    r = CliRunner().invoke(
        cli,
        [
            "speak", "Hello", "-o", str(tmp_path / "o.mp3"),
            "--model", "worder-tts", "--voice", "voice_abc123", "--dry-run",
        ],
    )
    assert r.exit_code == 0
    assert "📝" in r.output


def test_hint_worder_401():
    h = hint("worder", 401, "Unauthorized")
    assert "WORDER_API_KEY" in h


def test_hint_worder_422_quality():
    h = hint("worder", 422, '{"quality_failed": true, "similarity": 0.7}')
    assert "quality" in h.lower()
