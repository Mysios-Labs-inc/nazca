"""ElevenLabs sound effects (audio modality, op="sfx") — issue #122 phase A3.

Mirrors tests/test_elevenlabs_audio.py's structure/depth for the second
ElevenLabs op: no --voice, no voice_id URL segment, a `duration_seconds`
field instead of `lyrics`/`voice`, and — the gap the A1/A2 reviews caught —
validate_op actually REJECTS "sfx" for every other existing audio model, not
just accepts it for elevenlabs-sfx.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest import mock

from click.testing import CliRunner

from nazca.audio import generate_sfx, speak
from nazca.backends.elevenlabs import ElevenLabsBackend, ElevenLabsError, ElevenLabsRateLimitError
from nazca.capabilities import CapabilityError, validate_op
from nazca.cli import cli
from nazca.cost import estimate_audio_cost


def test_generate_sfx_dry_run_plan(tmp_path):
    plan_path = generate_sfx(tmp_path / "effect.mp3", "glass breaking on concrete", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["backend"] == "elevenlabs"
    assert plan["url"] == "https://api.elevenlabs.io/v1/sound-generation?output_format=mp3_44100_128"
    assert plan["body"] == {"text": "glass breaking on concrete"}
    assert "voice" not in plan["body"] and "model_id" not in plan["body"]  # not TTS shape


def test_generate_sfx_dry_run_includes_duration_when_given(tmp_path):
    plan_path = generate_sfx(tmp_path / "rain.mp3", "heavy rainfall", duration_seconds=8, dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["body"]["duration_seconds"] == 8


def test_generate_sfx_omits_duration_when_not_given(tmp_path):
    plan_path = generate_sfx(tmp_path / "effect.mp3", "a short beep", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert "duration_seconds" not in plan["body"]


def test_generate_sfx_default_model_is_elevenlabs_sfx(tmp_path):
    plan_path = generate_sfx(tmp_path / "effect.mp3", "a prompt", dry_run=True)
    plan = json.loads(plan_path.read_text())
    # distinguishes elevenlabs-sfx from elevenlabs-tts, which shares the same backend
    assert plan["url"] == "https://api.elevenlabs.io/v1/sound-generation?output_format=mp3_44100_128"


def test_generate_sfx_forwards_wav_format(tmp_path):
    plan_path = generate_sfx(tmp_path / "effect.wav", "a prompt", output_format="wav", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["url"] == "https://api.elevenlabs.io/v1/sound-generation?output_format=wav_44100"


def test_speak_op_sfx_never_touches_network_or_auth_with_key_unset(tmp_path, monkeypatch):
    # Direct regression test for the exact bug class phase A2's voice_design
    # shipped (auth touched before the dry_run check).
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    with mock.patch("urllib.request.urlopen") as urlopen:
        speak(tmp_path / "effect.mp3", "a prompt", model="elevenlabs-sfx", op="sfx", dry_run=True)
        urlopen.assert_not_called()


def test_validate_op_accepts_sfx_for_elevenlabs_sfx():
    validate_op("elevenlabs-sfx", "sfx")  # must not raise


def test_validate_op_rejects_sfx_for_every_other_audio_model():
    other_audio_models = [
        "atlas-tts-grok", "atlas-tts-elevenlabs-v3", "worder-tts", "fish-tts",
        "fish-voice-clone", "fish-voice-design", "elevenlabs-tts", "atlas-music-minimax",
    ]
    for sh in other_audio_models:
        try:
            validate_op(sh, "sfx")
        except CapabilityError:
            pass
        else:
            raise AssertionError(f"expected CapabilityError: {sh} should not support sfx")


def test_validate_op_rejects_tts_for_elevenlabs_sfx():
    try:
        validate_op("elevenlabs-sfx", "tts")
    except CapabilityError:
        pass
    else:
        raise AssertionError("elevenlabs-sfx should not support tts")


def test_audio_cost_sfx_unpriced():
    assert estimate_audio_cost("elevenlabs-sfx") is None


def test_cli_sfx_dry_run(tmp_path):
    r = CliRunner().invoke(
        cli, ["sfx", "glass breaking on concrete", "-o", str(tmp_path / "effect.mp3"), "--dry-run"],
    )
    assert r.exit_code == 0
    assert "📝" in r.output


def test_cli_sfx_with_duration_dry_run(tmp_path):
    r = CliRunner().invoke(
        cli,
        [
            "sfx", "heavy rainfall", "--duration", "8",
            "-o", str(tmp_path / "rain.mp3"), "--dry-run",
        ],
    )
    assert r.exit_code == 0
    plan = json.loads((tmp_path / "rain.request.json").read_text())
    assert plan["body"]["duration_seconds"] == 8.0


def test_cli_sfx_backend_error_is_clean_not_a_traceback(tmp_path, monkeypatch):
    # ElevenLabsError subclasses BackendError — the CLI's `sfx` command must
    # catch it via the same except BackendError pattern as speak/music (not
    # the narrower AudioError catch that was the real bug fixed earlier in
    # this issue).
    def raise_elevenlabs_error(self, resolved, req):
        raise ElevenLabsError("ElevenLabs HTTP 401: invalid key")

    monkeypatch.setattr(ElevenLabsBackend, "run_audio", raise_elevenlabs_error)
    r = CliRunner().invoke(cli, ["sfx", "a prompt", "-o", str(tmp_path / "effect.mp3")])
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit)
    assert "❌" in r.output
    assert "invalid key" in r.output


# --------------------------------------------------------------------------- end-to-end (real HTTP dispatch, mocked urlopen)


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


def _clear_real_config_attr(name: str) -> None:
    import nazca.config as config

    config.__dict__.pop(name, None)


def _sfx_request(duration_seconds=None):
    from nazca.request import AudioRequest

    return AudioRequest(text="glass breaking on concrete", op="sfx", duration_seconds=duration_seconds, dry_run=False)


def test_run_audio_sfx_success_returns_raw_audio_bytes(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-sfx", "audio")
    req = _sfx_request(duration_seconds=8)
    audio_bytes = b"\xff\xfb not real mp3 but stands in for one"

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return audio_bytes

    captured_requests = []

    def fake_urlopen(request, timeout=None):
        captured_requests.append(request)
        return _Resp()

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        out = ElevenLabsBackend().run_audio(resolved, req)
    assert out == audio_bytes
    sent = captured_requests[0]
    assert sent.get_header("Xi-api-key") == "test-key"
    assert "/v1/sound-generation" in sent.full_url
    assert "/v1/text-to-speech" not in sent.full_url  # not the TTS endpoint
    sent_body = json.loads(sent.data.decode())
    assert sent_body == {"text": "glass breaking on concrete", "duration_seconds": 8}


def test_run_audio_sfx_success_omits_duration_when_not_given(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-sfx", "audio")
    req = _sfx_request()

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"audio bytes"

    captured_requests = []

    def fake_urlopen(request, timeout=None):
        captured_requests.append(request)
        return _Resp()

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        ElevenLabsBackend().run_audio(resolved, req)
    sent_body = json.loads(captured_requests[0].data.decode())
    assert sent_body == {"text": "glass breaking on concrete"}


def test_run_audio_sfx_missing_api_key_raises_before_network(monkeypatch):
    # Non-dry-run counterpart to the dry-run regression test above — pins that
    # the sfx branch's url/body construction still can't accidentally reach
    # `_post`/`auth_token()` without a key, the same bug class A2's
    # voice_design shipped once for a different ElevenLabs/Fish op.
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-sfx", "audio")
    req = _sfx_request()
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            ElevenLabsBackend().run_audio(resolved, req)
        except ElevenLabsError as e:
            assert "ELEVENLABS_API_KEY" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError when ELEVENLABS_API_KEY is unset")
        urlopen.assert_not_called()


def test_run_audio_sfx_http_error_wraps_as_elevenlabs_error(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-sfx", "audio")
    req = _sfx_request()

    def raise_422(req, timeout=None):
        raise _http_error(422, '{"detail": [{"loc": ["body", "text"], "msg": "field required"}]}')

    with mock.patch("urllib.request.urlopen", raise_422):
        try:
            ElevenLabsBackend().run_audio(resolved, req)
        except ElevenLabsError as e:
            assert "422" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError on HTTP 422")


def test_run_audio_sfx_persistent_429_raises_rate_limit_error(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-sfx", "audio")
    req = _sfx_request()

    def raise_429(req, timeout=None):
        raise _http_error(429, "rate limited")

    with mock.patch("urllib.request.urlopen", raise_429):
        try:
            ElevenLabsBackend().run_audio(resolved, req)
        except ElevenLabsRateLimitError:
            pass
        else:
            raise AssertionError("expected ElevenLabsRateLimitError on persisted HTTP 429")
