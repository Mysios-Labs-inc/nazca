"""ElevenLabs speech-to-speech voice conversion (audio modality, op=
"speech_to_speech") — issue #122 phase A3.

Mirrors tests/test_elevenlabs_sfx.py's structure/depth for the third
ElevenLabs op: a genuinely new shape (local source audio FILE + voice_id in,
audio out) that goes through a dedicated `SpeechToSpeechRequest`/
`nazca.voice.speech_to_speech` seam instead of `AudioRequest`/`audio.speak`,
and a multipart *request* whose *response* is still raw audio bytes (neither
`retry.post_multipart` nor `retry.post_bytes` covers that combination alone,
hence `retry.post_multipart_bytes`).
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest import mock

from click.testing import CliRunner

from nazca.backends.elevenlabs import ElevenLabsBackend, ElevenLabsError, ElevenLabsRateLimitError
from nazca.capabilities import CapabilityError, validate_op
from nazca.cli import cli
from nazca.cost import estimate_audio_cost
from nazca.voice import speech_to_speech


def _sample(tmp_path, name="sample.mp3", content=b"not real mp3 but stands in for one"):
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_speech_to_speech_dry_run_plan(tmp_path):
    src = _sample(tmp_path)
    plan_path = speech_to_speech(
        str(src), out=str(tmp_path / "out.mp3"), voice="21m00Tcm4TlvDq8ikWAM", dry_run=True,
    )
    plan = json.loads(plan_path.read_text())
    assert plan["backend"] == "elevenlabs"
    assert plan["url"] == (
        "https://api.elevenlabs.io/v1/speech-to-speech/21m00Tcm4TlvDq8ikWAM"
        "?output_format=mp3_44100_128"
    )
    assert plan["fields"] == {"model_id": "eleven_english_sts_v2"}
    assert plan["files"] == [{"field": "audio", "filename": "sample.mp3", "size_bytes": len(src.read_bytes())}]
    assert plan["headers"] == {}


def test_speech_to_speech_dry_run_forwards_wav_format(tmp_path):
    src = _sample(tmp_path)
    plan_path = speech_to_speech(
        str(src), out=str(tmp_path / "out.wav"), voice="v1", output_format="wav", dry_run=True,
    )
    plan = json.loads(plan_path.read_text())
    assert plan["url"] == "https://api.elevenlabs.io/v1/speech-to-speech/v1?output_format=wav_44100"


def test_speech_to_speech_requires_voice(tmp_path):
    from nazca.request import SpeechToSpeechRequest
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-speech-to-speech", "audio")
    src = _sample(tmp_path)
    req = SpeechToSpeechRequest(source_audio_path=str(src), voice=None, dry_run=True)
    try:
        ElevenLabsBackend().speech_to_speech(resolved, req)
    except ElevenLabsError as e:
        assert "voice" in str(e).lower()
    else:
        raise AssertionError("expected ElevenLabsError when no voice is given")


def test_speech_to_speech_missing_source_file_raises_before_network(tmp_path):
    from nazca.request import SpeechToSpeechRequest
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-speech-to-speech", "audio")
    missing = tmp_path / "does-not-exist.mp3"
    req = SpeechToSpeechRequest(source_audio_path=str(missing), voice="v1", dry_run=False)
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            ElevenLabsBackend().speech_to_speech(resolved, req)
        except ElevenLabsError as e:
            assert "not a file" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError for a missing source file")
        urlopen.assert_not_called()


def test_speech_to_speech_never_touches_network_auth_or_disk_read_on_dry_run(tmp_path, monkeypatch):
    # Direct regression test for the exact bug class phase A2's voice_design
    # shipped (auth touched before the dry_run check) — here also pins that
    # dry-run only stats the file (for size), never reads its bytes.
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    src = _sample(tmp_path)
    with mock.patch("urllib.request.urlopen") as urlopen:
        speech_to_speech(
            str(src), out=str(tmp_path / "out.mp3"), voice="v1", dry_run=True,
        )
        urlopen.assert_not_called()


def test_validate_op_accepts_speech_to_speech_for_elevenlabs_speech_to_speech():
    validate_op("elevenlabs-speech-to-speech", "speech_to_speech")  # must not raise


def test_validate_op_rejects_speech_to_speech_for_every_other_audio_model():
    other_audio_models = [
        "atlas-tts-grok", "atlas-tts-elevenlabs-v3", "worder-tts", "fish-tts",
        "fish-voice-clone", "fish-voice-design", "elevenlabs-tts", "elevenlabs-sfx",
        "atlas-music-minimax",
    ]
    for sh in other_audio_models:
        try:
            validate_op(sh, "speech_to_speech")
        except CapabilityError:
            pass
        else:
            raise AssertionError(f"expected CapabilityError: {sh} should not support speech_to_speech")


def test_validate_op_rejects_tts_for_elevenlabs_speech_to_speech():
    try:
        validate_op("elevenlabs-speech-to-speech", "tts")
    except CapabilityError:
        pass
    else:
        raise AssertionError("elevenlabs-speech-to-speech should not support tts")


def test_audio_cost_speech_to_speech_unpriced():
    assert estimate_audio_cost("elevenlabs-speech-to-speech") is None


def test_cli_speech_to_speech_dry_run(tmp_path):
    src = _sample(tmp_path)
    r = CliRunner().invoke(
        cli,
        [
            "speech-to-speech", str(src), "--voice", "v1",
            "-o", str(tmp_path / "out.mp3"), "--dry-run",
        ],
    )
    assert r.exit_code == 0
    assert "📝" in r.output


def test_cli_speech_to_speech_requires_existing_source(tmp_path):
    r = CliRunner().invoke(
        cli,
        [
            "speech-to-speech", str(tmp_path / "nope.mp3"), "--voice", "v1",
            "-o", str(tmp_path / "out.mp3"), "--dry-run",
        ],
    )
    assert r.exit_code != 0


def test_cli_speech_to_speech_backend_error_is_clean_not_a_traceback(tmp_path):
    # ElevenLabsError subclasses BackendError — the CLI's `speech-to-speech`
    # command must catch it via the same except BackendError pattern as
    # speak/music/sfx (not the narrower AudioError catch that was the real
    # bug fixed earlier in this issue).
    src = _sample(tmp_path)

    def raise_elevenlabs_error(self, resolved, req):
        raise ElevenLabsError("ElevenLabs HTTP 401: invalid key")

    with mock.patch.object(ElevenLabsBackend, "speech_to_speech", raise_elevenlabs_error):
        r = CliRunner().invoke(
            cli,
            ["speech-to-speech", str(src), "--voice", "v1", "-o", str(tmp_path / "out.mp3")],
        )
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


class _Resp:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_run_speech_to_speech_success_returns_raw_audio_bytes(tmp_path, monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    from nazca.request import SpeechToSpeechRequest
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-speech-to-speech", "audio")
    src = _sample(tmp_path, content=b"source audio bytes")
    req = SpeechToSpeechRequest(source_audio_path=str(src), voice="v1", dry_run=False)
    audio_bytes = b"\xff\xfb not real mp3 but stands in for one"

    captured_requests = []

    def fake_urlopen(request, timeout=None):
        captured_requests.append(request)
        return _Resp(audio_bytes)

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        out = ElevenLabsBackend().speech_to_speech(resolved, req)
    assert out == audio_bytes
    sent = captured_requests[0]
    assert sent.get_header("Xi-api-key") == "test-key"
    assert "/v1/speech-to-speech/v1" in sent.full_url
    assert sent.get_header("Content-type", "").startswith("multipart/form-data; boundary=")
    body = sent.data
    assert b'name="model_id"' in body
    assert b"eleven_english_sts_v2" in body
    assert b'name="audio"; filename="' in body
    assert b"source audio bytes" in body


def test_run_speech_to_speech_missing_api_key_raises_before_network(tmp_path, monkeypatch):
    # Non-dry-run counterpart to the dry-run regression test above — pins that
    # speech_to_speech's file-read/url/fields construction still can't
    # accidentally reach `_post`/`auth_token()` without a key.
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    from nazca.request import SpeechToSpeechRequest
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-speech-to-speech", "audio")
    src = _sample(tmp_path)
    req = SpeechToSpeechRequest(source_audio_path=str(src), voice="v1", dry_run=False)
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            ElevenLabsBackend().speech_to_speech(resolved, req)
        except ElevenLabsError as e:
            assert "ELEVENLABS_API_KEY" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError when ELEVENLABS_API_KEY is unset")
        urlopen.assert_not_called()


def test_run_speech_to_speech_http_error_wraps_as_elevenlabs_error(tmp_path, monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    from nazca.request import SpeechToSpeechRequest
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-speech-to-speech", "audio")
    src = _sample(tmp_path)
    req = SpeechToSpeechRequest(source_audio_path=str(src), voice="v1", dry_run=False)

    def raise_422(req, timeout=None):
        raise _http_error(422, '{"detail": [{"loc": ["body", "audio"], "msg": "field required"}]}')

    with mock.patch("urllib.request.urlopen", raise_422):
        try:
            ElevenLabsBackend().speech_to_speech(resolved, req)
        except ElevenLabsError as e:
            assert "422" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError on HTTP 422")


def test_run_speech_to_speech_persistent_429_raises_rate_limit_error(tmp_path, monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    from nazca.request import SpeechToSpeechRequest
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-speech-to-speech", "audio")
    src = _sample(tmp_path)
    req = SpeechToSpeechRequest(source_audio_path=str(src), voice="v1", dry_run=False)

    def raise_429(req, timeout=None):
        raise _http_error(429, "rate limited")

    with mock.patch("urllib.request.urlopen", raise_429):
        try:
            ElevenLabsBackend().speech_to_speech(resolved, req)
        except ElevenLabsRateLimitError:
            pass
        else:
            raise AssertionError("expected ElevenLabsRateLimitError on persisted HTTP 429")


def test_run_speech_to_speech_non_media_content_type_raises(tmp_path, monkeypatch):
    # A 2xx body whose Content-Type is JSON must not be written to disk as if
    # it were real audio (retry.post_multipart_bytes's guard).
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    from nazca.request import SpeechToSpeechRequest
    from nazca.resolve import resolve

    resolved = resolve("elevenlabs-speech-to-speech", "audio")
    src = _sample(tmp_path)
    req = SpeechToSpeechRequest(source_audio_path=str(src), voice="v1", dry_run=False)

    def fake_urlopen(request, timeout=None):
        return _Resp(b'{"error": "oops"}', headers={"Content-Type": "application/json"})

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        try:
            ElevenLabsBackend().speech_to_speech(resolved, req)
        except ElevenLabsError as e:
            assert "not audio" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError for a JSON 2xx body")
