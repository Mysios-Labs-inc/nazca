"""ElevenLabs TTS (audio modality) — dry-run plan, missing-voice error, cost, error hints.

Mirrors tests/test_fish_audio.py's structure/depth for the fourth direct speech
backend (issue #122 phase A3) — same dry-run/error/rate-limit coverage, adapted
for ElevenLabs' three structural differences: `xi-api-key` auth (not Bearer),
voice_id as a URL path segment (not a body field), and output_format as a query
string parameter (not a body field).
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest import mock

from click.testing import CliRunner

from nazca.audio import speak
from nazca.backends.elevenlabs import ElevenLabsBackend, ElevenLabsError, ElevenLabsRateLimitError
from nazca.backends.error_hints import hint
from nazca.cli import cli
from nazca.cost import estimate_audio_cost
from nazca.resolve import resolve


def test_speak_dry_run_plan_requires_voice(tmp_path):
    out = tmp_path / "hi.mp3"
    plan_path = speak(out, "Hello world", model="elevenlabs-tts", voice="voice_abc123", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["backend"] == "elevenlabs"
    # mp3 (the AudioRequest default) maps to ElevenLabs' own default output_format enum.
    assert plan["url"] == (
        "https://api.elevenlabs.io/v1/text-to-speech/voice_abc123?output_format=mp3_44100_128"
    )
    assert plan["body"] == {"text": "Hello world", "model_id": "eleven_multilingual_v2"}
    # xi-api-key must never appear in a dry-run plan.
    assert "headers" in plan
    assert "xi-api-key" not in json.dumps(plan)
    assert "Authorization" not in plan.get("headers", {})


def test_speak_dry_run_plan_forwards_wav_format(tmp_path):
    out = tmp_path / "hi.wav"
    plan_path = speak(
        out, "Hello world", model="elevenlabs-tts", voice="voice_abc123",
        output_format="wav", dry_run=True,
    )
    plan = json.loads(plan_path.read_text())
    assert plan["url"] == (
        "https://api.elevenlabs.io/v1/text-to-speech/voice_abc123?output_format=wav_44100"
    )


def test_tts_endpoint_omits_query_param_when_output_format_unset():
    url = ElevenLabsBackend().tts_endpoint("voice_abc123")
    assert url == "https://api.elevenlabs.io/v1/text-to-speech/voice_abc123"
    assert "output_format" not in url


def test_speak_without_voice_raises():
    # Note: ElevenLabsError is a BackendError, not an AudioError, mirroring
    # Worder/Fish/Atlas — the audio orchestrator only wraps the missing-model
    # case, not backend-specific dispatch failures.
    try:
        speak("/tmp/wont-be-written.mp3", "hi", model="elevenlabs-tts", dry_run=True)
    except ElevenLabsError as e:
        assert "voice" in str(e).lower()
    else:
        raise AssertionError("expected ElevenLabsError when no --voice is given")


def test_audio_cost_unpriced():
    # ElevenLabs pricing is subscription-tier-based, not a simple per-request rate.
    assert estimate_audio_cost("elevenlabs-tts", chars=1000) is None


def test_cli_speak_elevenlabs_dry_run(tmp_path):
    r = CliRunner().invoke(
        cli,
        [
            "speak", "Hello", "-o", str(tmp_path / "o.mp3"),
            "--model", "elevenlabs-tts", "--voice", "voice_abc123", "--dry-run",
        ],
    )
    assert r.exit_code == 0
    assert "📝" in r.output


def test_cli_speak_backend_error_is_clean_not_a_traceback(tmp_path, monkeypatch):
    # ElevenLabsError subclasses BackendError, not AudioError — the CLI's `speak`
    # command catches BackendError (see issue #122 A2's review finding, already
    # fixed for Fish/Worder/Atlas), so a real ElevenLabs HTTP error should surface
    # as a clean "❌ ..." + exit 1, not an unhandled traceback.
    def raise_elevenlabs_error(self, resolved, req):
        raise ElevenLabsError("ElevenLabs HTTP 401: invalid key")

    monkeypatch.setattr(ElevenLabsBackend, "run_audio", raise_elevenlabs_error)
    r = CliRunner().invoke(
        cli,
        [
            "speak", "Hello", "-o", str(tmp_path / "o.mp3"),
            "--model", "elevenlabs-tts", "--voice", "voice_abc123",
        ],
    )
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit)
    assert "❌" in r.output
    assert "invalid key" in r.output


def test_hint_elevenlabs_401_invalid_key():
    h = hint("elevenlabs", 401, "Unauthorized")
    assert "ELEVENLABS_API_KEY" in h


def test_hint_elevenlabs_401_quota_exceeded():
    # ElevenLabs returns quota/credit exhaustion as HTTP 401 (status "quota_exceeded"
    # in the body), NOT HTTP 402 — verified against their own error-messages docs.
    h = hint("elevenlabs", 401, '{"detail": {"status": "quota_exceeded", "message": "..."}}')
    assert "quota" in h.lower() or "credit" in h.lower()


def test_hint_elevenlabs_422_validation():
    h = hint(
        "elevenlabs", 422,
        '{"detail": [{"loc": ["body", "text"], "type": "missing", "msg": "field required"}]}',
    )
    assert "validation" in h.lower()


def test_hint_elevenlabs_429():
    h = hint("elevenlabs", 429, '{"detail": {"status": "too_many_concurrent_requests"}}')
    assert "rate" in h.lower() or "concurren" in h.lower()


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


def _elevenlabs_request(voice="voice_abc123"):
    from nazca.request import AudioRequest

    resolved = resolve("elevenlabs-tts", "audio")
    req = AudioRequest(text="hi", voice=voice, dry_run=False)
    return resolved, req


def _clear_real_config_attr(name: str) -> None:
    """Remove a real module attribute if it exists, so config.__getattr__'s
    fresh env/ini resolution isn't shadowed by a stale value from a prior test.
    """
    import nazca.config as config

    config.__dict__.pop(name, None)


def test_run_audio_missing_api_key_raises_before_network(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    resolved, req = _elevenlabs_request()
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            ElevenLabsBackend().run_audio(resolved, req)
        except ElevenLabsError as e:
            assert "ELEVENLABS_API_KEY" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError when ELEVENLABS_API_KEY is unset")
        urlopen.assert_not_called()


def test_dry_run_never_touches_auth_token_even_with_key_unset(monkeypatch):
    # A2's voice_design shipped a bug where headers were built before the
    # dry_run check, so it quietly needed a key even in dry-run. Trace this
    # explicitly for ElevenLabs: dry_run=True with the key fully unset must
    # neither raise nor touch the network.
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    resolved, req = _elevenlabs_request()
    req.dry_run = True
    with mock.patch("urllib.request.urlopen") as urlopen:
        plan = ElevenLabsBackend().run_audio(resolved, req)
        urlopen.assert_not_called()
    assert plan["backend"] == "elevenlabs"


def test_run_audio_success_returns_raw_audio_bytes(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    resolved, req = _elevenlabs_request()
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
    # Confirm the real request (built via urllib.request.Request, not just the
    # backend's own method) carries xi-api-key and the voice_id-in-URL shape.
    sent = captured_requests[0]
    assert sent.get_header("Xi-api-key") == "test-key"
    assert sent.get_header("Authorization") is None
    assert "/v1/text-to-speech/voice_abc123" in sent.full_url
    assert "output_format=mp3_44100_128" in sent.full_url
    sent_body = json.loads(sent.data.decode())
    assert sent_body == {"text": "hi", "model_id": "eleven_multilingual_v2"}


def test_run_audio_http_error_wraps_as_elevenlabs_error_with_hint(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    resolved, req = _elevenlabs_request()

    def raise_401(req, timeout=None):
        raise _http_error(401, "Unauthorized")

    with mock.patch("urllib.request.urlopen", raise_401):
        try:
            ElevenLabsBackend().run_audio(resolved, req)
        except ElevenLabsError as e:
            assert "401" in str(e)
            assert "ELEVENLABS_API_KEY" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError on HTTP 401")


def test_run_audio_persistent_429_raises_elevenlabs_rate_limit_error(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    monkeypatch.setenv("NAZCA_BACKOFF_BASE", "0")
    resolved, req = _elevenlabs_request()

    def raise_429(req, timeout=None):
        raise _http_error(429, "rate limited")

    with mock.patch("urllib.request.urlopen", raise_429):
        try:
            ElevenLabsBackend().run_audio(resolved, req)
        except ElevenLabsRateLimitError:
            pass
        else:
            raise AssertionError("expected ElevenLabsRateLimitError on persisted HTTP 429")
