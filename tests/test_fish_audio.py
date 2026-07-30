"""Fish Audio TTS (audio modality) — dry-run plan, missing-voice error, cost, error hints."""

from __future__ import annotations

import json

from click.testing import CliRunner

from nazca.audio import speak
from nazca.backends.error_hints import hint
from nazca.backends.fish import FishError
from nazca.cli import cli
from nazca.cost import estimate_audio_cost


def test_speak_dry_run_plan_requires_voice(tmp_path):
    out = tmp_path / "hi.mp3"
    plan_path = speak(out, "Hello world", model="fish-tts", voice="ref_abc123", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["backend"] == "fish"
    assert plan["url"] == "https://api.fish.audio/v1/tts"
    assert plan["body"] == {"reference_id": "ref_abc123", "text": "Hello world"}
    assert plan["headers"] == {"model": "s2-pro"}


def test_speak_without_voice_raises():
    # Note: FishError is a BackendError, not an AudioError, mirroring Worder/Atlas —
    # the audio orchestrator only wraps the missing-model case, not backend-specific
    # dispatch failures.
    try:
        speak("/tmp/wont-be-written.mp3", "hi", model="fish-tts", dry_run=True)
    except FishError as e:
        assert "voice" in str(e).lower()
    else:
        raise AssertionError("expected FishError when no --voice is given")


def test_audio_cost_unpriced():
    # Fish Audio pricing is unverified against a live key — not in the flat-rate table.
    assert estimate_audio_cost("fish-tts", chars=1000) is None


def test_cli_speak_fish_dry_run(tmp_path):
    r = CliRunner().invoke(
        cli,
        [
            "speak", "Hello", "-o", str(tmp_path / "o.mp3"),
            "--model", "fish-tts", "--voice", "ref_abc123", "--dry-run",
        ],
    )
    assert r.exit_code == 0
    assert "📝" in r.output


def test_hint_fish_401():
    h = hint("fish", 401, "Unauthorized")
    assert "FISH_API_KEY" in h


def test_hint_fish_402():
    h = hint("fish", 402, "Insufficient credits")
    assert "credit" in h.lower()


def test_hint_fish_422_validation():
    h = hint("fish", 422, '[{"loc": ["body", "text"], "type": "missing", "msg": "field required"}]')
    assert "validation" in h.lower()
