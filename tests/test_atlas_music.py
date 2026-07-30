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
    assert plan["body"] == {
        "model": "minimax/music-2.6", "prompt": "warm acoustic folk, gentle guitar", "format": "mp3",
    }
    assert "text" not in plan["body"] and "voice" not in plan["body"]  # not TTS shape
    assert plan["est_cost_usd"] == 0.15


def test_generate_music_forwards_wav_format(tmp_path):
    plan_path = generate_music(
        tmp_path / "track.wav", "ambient drone", output_format="wav", dry_run=True,
    )
    plan = json.loads(plan_path.read_text())
    assert plan["body"]["format"] == "wav"


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


def test_flat_rate_audio_pricing_does_not_shadow_any_existing_tts_model():
    # cost.py now checks _AUDIO_FLAT_PER_RUN before the per-char _TTS_PER_1K_CHARS
    # table — explicitly pin that no existing TTS model's price estimate silently
    # changed as a side effect, rather than relying on sibling test files
    # incidentally still passing to prove it.
    from nazca.cost import _AUDIO_FLAT_PER_RUN, _TTS_PER_1K_CHARS

    assert _TTS_PER_1K_CHARS.keys().isdisjoint(_AUDIO_FLAT_PER_RUN.keys())
    assert estimate_audio_cost("atlas-tts-grok", chars=1000).usd == 0.015
    assert estimate_audio_cost("atlas-tts-elevenlabs-v3", chars=1000).usd == 0.10


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


# --------------------------------------------------------------------------- end-to-end (real submit->poll->download dispatch)


class _Resp:
    """Minimal context-manager stand-in for a urllib response."""

    def __init__(self, payload: bytes, headers: dict | None = None):
        self._payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def test_run_audio_music_success_through_real_submit_poll_download(monkeypatch, tmp_path):
    # No existing Atlas test (any modality) exercised the real submit->poll->
    # download dispatch with a mocked urlopen — every prior test either drove
    # the dry-run branch or monkeypatched run_audio itself away. This is the
    # first, and it doubles as coverage for the new Content-Type guard in
    # _poll (a legit "audio/mpeg" response must pass through untouched).
    import json as jsonlib

    from nazca.backends.atlas import AtlasBackend
    from nazca.request import AudioRequest
    from nazca.resolve import resolve

    monkeypatch.setattr("nazca.config.get_value", lambda key: "test-key" if key == "atlas_api_key" else None)
    monkeypatch.delenv("ATLAS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.ATLAS_API_KEY", "test-key")
    monkeypatch.setattr("nazca.backends.atlas.time.sleep", lambda *a, **kw: None)

    raw_audio = b"totally-not-real-mp3-but-stands-in"
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:  # POST /model/generateAudio
            return _Resp(jsonlib.dumps({"data": {"id": "pred_123"}}).encode())
        if calls["n"] == 2:  # GET /model/prediction/pred_123
            return _Resp(jsonlib.dumps(
                {"data": {"status": "completed", "outputs": ["https://cdn.example/track.mp3"]}}
            ).encode())
        # GET the output URL itself
        return _Resp(raw_audio, headers={"Content-Type": "audio/mpeg"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    resolved = resolve("atlas-music-minimax", "audio")
    req = AudioRequest(text="warm acoustic folk", op="music", dry_run=False)
    result = AtlasBackend().run_audio(resolved, req)
    assert result == raw_audio
    assert calls["n"] == 3


def test_run_audio_rejects_error_body_disguised_as_completed_output(monkeypatch):
    # The Content-Type guard added alongside this PR: an expired signed URL or
    # a partial-failure response can return an error body (e.g. S3 XML) at
    # HTTP 200 — must raise AtlasError, not silently write it to the output file.
    import json as jsonlib

    from nazca.backends.atlas import AtlasBackend
    from nazca.request import AudioRequest
    from nazca.resolve import resolve

    monkeypatch.setattr("nazca.config.ATLAS_API_KEY", "test-key")
    monkeypatch.setattr("nazca.backends.atlas.time.sleep", lambda *a, **kw: None)
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(jsonlib.dumps({"data": {"id": "pred_123"}}).encode())
        if calls["n"] == 2:
            return _Resp(jsonlib.dumps(
                {"data": {"status": "completed", "outputs": ["https://cdn.example/expired"]}}
            ).encode())
        return _Resp(b"<Error><Code>AccessDenied</Code></Error>", headers={"Content-Type": "application/xml"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    resolved = resolve("atlas-music-minimax", "audio")
    req = AudioRequest(text="a prompt", op="music", dry_run=False)
    try:
        AtlasBackend().run_audio(resolved, req)
    except AtlasError as e:
        assert "doesn't look like media" in str(e)
    else:
        raise AssertionError("expected AtlasError for a non-media Content-Type")
