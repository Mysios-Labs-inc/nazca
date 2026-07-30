"""Atlas Cloud music generation (audio modality, op="music") — issue #122 phase A4.

Covers generate_music()'s dry-run plan (prompt/lyrics body shape, distinct from
TTS's text/voice/format shape), the flat-per-generation cost estimate, the CLI
command, and — the gap the A1/A2 reviews caught — that validate_op actually
REJECTS "music" for every other existing audio model, not just accepts it for
atlas-music-minimax.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from nazca.audio import generate_music, speak
from nazca.backends.atlas import AtlasError
from nazca.capabilities import CapabilityError, validate_op
from nazca.cli import cli
from nazca.cost import estimate_audio_cost


def test_generate_music_dry_run_plan(tmp_path):
    plan_path = generate_music(tmp_path / "track.mp3", "warm acoustic folk, gentle guitar", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["model"] == "minimax/music-2.6"  # standalone slug, no op suffix
    assert plan["backend"] == "atlas"
    assert plan["body"] == {"model": "minimax/music-2.6", "prompt": "warm acoustic folk, gentle guitar"}
    assert "text" not in plan["body"] and "voice" not in plan["body"]  # not TTS shape
    assert plan["est_cost_usd"] == 0.15


def test_generate_music_dry_run_includes_lyrics_when_given(tmp_path):
    plan_path = generate_music(
        tmp_path / "track.mp3", "upbeat synth-pop", lyrics="[Verse]\nWalking through the city lights",
        dry_run=True,
    )
    plan = json.loads(plan_path.read_text())
    assert plan["body"]["lyrics"] == "[Verse]\nWalking through the city lights"


def test_generate_music_omits_lyrics_when_not_given(tmp_path):
    plan_path = generate_music(tmp_path / "track.mp3", "ambient drone", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert "lyrics" not in plan["body"]


def test_generate_music_default_model_is_atlas_music_minimax(tmp_path):
    plan_path = generate_music(tmp_path / "track.mp3", "a prompt", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["model"] == "minimax/music-2.6"


def test_speak_op_music_never_touches_network_or_auth_with_key_unset(tmp_path, monkeypatch):
    # Direct regression test for the exact bug class phase A2's voice_design
    # shipped (auth touched before the dry_run check): assert urlopen is never
    # called, not just that the call "succeeds" (which a stale local config
    # file could mask, as A2's own CI run demonstrated).
    from unittest import mock

    monkeypatch.delenv("ATLAS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    with mock.patch("urllib.request.urlopen") as urlopen:
        speak(tmp_path / "track.mp3", "a prompt", model="atlas-music-minimax", op="music", dry_run=True)
        urlopen.assert_not_called()


def test_validate_op_accepts_music_for_atlas_music_minimax():
    validate_op("atlas-music-minimax", "music")  # must not raise


def test_validate_op_rejects_music_for_every_other_audio_model():
    other_audio_models = [
        "atlas-tts-grok", "atlas-tts-elevenlabs-v3", "worder-tts", "fish-tts",
        "fish-voice-clone", "fish-voice-design", "elevenlabs-tts",
    ]
    for sh in other_audio_models:
        try:
            validate_op(sh, "music")
        except CapabilityError:
            pass
        else:
            raise AssertionError(f"expected CapabilityError: {sh} should not support music")


def test_speak_rejects_tts_op_for_atlas_music_minimax():
    # Cross-rejection the other direction: the music model shouldn't accept tts.
    try:
        validate_op("atlas-music-minimax", "tts")
    except CapabilityError:
        pass
    else:
        raise AssertionError("atlas-music-minimax should not support tts")


def test_audio_cost_music_is_flat_per_generation():
    est = estimate_audio_cost("atlas-music-minimax")
    assert est is not None
    assert est.usd == 0.15
    # chars is irrelevant for a flat-rate op — passing it changes nothing.
    assert estimate_audio_cost("atlas-music-minimax", chars=99999).usd == 0.15


def test_cli_music_dry_run(tmp_path):
    r = CliRunner().invoke(
        cli, ["music", "warm acoustic folk", "-o", str(tmp_path / "track.mp3"), "--dry-run"],
    )
    assert r.exit_code == 0
    assert "📝" in r.output
    assert "💵 ~$0.15" in r.output


def test_cli_music_with_lyrics_dry_run(tmp_path):
    r = CliRunner().invoke(
        cli,
        [
            "music", "upbeat synth-pop", "--lyrics", "[Verse]\\nhello",
            "-o", str(tmp_path / "track.mp3"), "--dry-run",
        ],
    )
    assert r.exit_code == 0
    plan = json.loads((tmp_path / "track.request.json").read_text())
    assert plan["body"]["lyrics"] == "[Verse]\\nhello"


def test_cli_music_backend_error_is_clean_not_a_traceback(tmp_path, monkeypatch):
    # AtlasError subclasses BackendError — the CLI's `music` command must catch
    # it via the same except BackendError pattern as speak/voice-clone/voice-design
    # (not a narrower AudioError catch, which was the real bug fixed earlier in
    # this issue).
    from nazca.backends.atlas import AtlasBackend

    def raise_atlas_error(self, resolved, req):
        raise AtlasError("Atlas HTTP 401: invalid key")

    monkeypatch.setattr(AtlasBackend, "run_audio", raise_atlas_error)
    r = CliRunner().invoke(cli, ["music", "a prompt", "-o", str(tmp_path / "track.mp3")])
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit)
    assert "❌" in r.output
    assert "invalid key" in r.output
