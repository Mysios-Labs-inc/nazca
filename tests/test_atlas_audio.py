"""Atlas Cloud TTS (audio modality) — dry-run plan + cost (PR6)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from nazca.audio import speak
from nazca.capabilities import CapabilityError
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


def test_speak_threads_op_to_audio_request(tmp_path, monkeypatch):
    # audio.speak() used to hardcode AudioRequest(op="tts", ...) — issue #122 A1
    # widened it to a real `op` kwarg. Assert directly on the AudioRequest built
    # inside speak() (not on a plan dict that happens to look the same for every
    # op) so a regression back to a hardcoded "tts" would actually fail this.
    captured = {}

    def fake_run_audio(self, resolved, req):
        captured["op"] = req.op
        return {"dry_run": True}

    monkeypatch.setattr("nazca.backends.atlas.AtlasBackend.run_audio", fake_run_audio)
    speak(tmp_path / "hi.mp3", "hi", model="atlas-tts-grok", op="tts", dry_run=True)
    assert captured["op"] == "tts"


def test_speak_rejects_op_no_audio_model_supports(tmp_path):
    # No audio model declares anything but "tts" yet (issue #122 A1 names the
    # vocabulary; A2+ wires it per provider). speak() must reject an unsupported
    # op via capabilities.validate_op rather than silently falling back to plain
    # TTS (Atlas's _model_slug) or ignoring req.op outright (Worder/Fish).
    with pytest.raises(CapabilityError, match="voice_clone"):
        speak(tmp_path / "hi.mp3", "hi", model="atlas-tts-grok", op="voice_clone", dry_run=True)


def test_audio_cost():
    est = estimate_audio_cost("atlas-tts-grok", chars=1000)
    assert est is not None
    assert est.usd == 0.015
    assert estimate_audio_cost("nonexistent", chars=1000) is None


def test_cli_speak_dry_run(tmp_path):
    r = CliRunner().invoke(cli, ["speak", "Hello", "-o", str(tmp_path / "o.mp3"), "--dry-run"])
    assert r.exit_code == 0
    assert "💵" in r.output  # cost label shown
