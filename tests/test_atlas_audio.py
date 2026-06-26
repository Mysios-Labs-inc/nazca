"""Atlas Cloud TTS (audio modality) — dry-run plan + cost (PR6)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from nazca.audio import speak
from nazca.cli import cli
from nazca.cost import estimate_audio_cost


def test_speak_dry_run_plan(tmp_path):
    out = tmp_path / "hi.mp3"
    plan_path = speak(out, "Hello world", model="atlas-tts-grok", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["model"] == "xai/tts-v1"  # standalone slug, no op suffix
    assert plan["backend"] == "atlas"
    assert plan["body"]["text"] == "Hello world"


def test_speak_elevenlabs_slug(tmp_path):
    plan_path = speak(tmp_path / "v.mp3", "hi", model="atlas-tts-elevenlabs-v3", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["model"] == "elevenlabs/v3/text-to-speech"  # stem + tts suffix


def test_audio_cost():
    est = estimate_audio_cost("atlas-tts-grok", chars=1000)
    assert est is not None
    assert est.usd == 0.015
    assert estimate_audio_cost("nonexistent", chars=1000) is None


def test_cli_speak_dry_run(tmp_path):
    r = CliRunner().invoke(cli, ["speak", "Hello", "-o", str(tmp_path / "o.mp3"), "--dry-run"])
    assert r.exit_code == 0
    assert "💵" in r.output  # cost label shown
