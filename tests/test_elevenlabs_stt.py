"""ElevenLabs speech-to-text (issue #122 phase A3, `stt`) — the first op in the
audio modality whose real output is text/JSON, not audio bytes, and whose
input is a local audio *file* rather than a text prompt.

Mirrors tests/test_elevenlabs_sfx.py's depth (dry-run plan tests, a
dry-run-never-touches-auth regression test, validate_op tested in both
directions, end-to-end success + error paths against mocked urlopen), plus
the file-handling edge cases `voice_clone` (Fish, phase A2) established as
precedent for a local-file-in op: missing path, OSError-at-read-time, and
"dry run never embeds the raw audio bytes".
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
from nazca.request import TranscriptionRequest
from nazca.transcribe import transcribe


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


def _clear_real_config_attr(name: str) -> None:
    import nazca.config as config

    config.__dict__.pop(name, None)


# --------------------------------------------------------------------------- dry-run plan


def test_transcribe_dry_run_plan(tmp_path):
    sample = tmp_path / "interview.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    plan_path = transcribe(tmp_path / "out.json", sample, dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["backend"] == "elevenlabs"
    assert plan["url"] == "https://api.elevenlabs.io/v1/speech-to-text"
    assert plan["fields"] == {"model_id": "scribe_v2"}
    assert plan["file"] == {"field": "file", "filename": "interview.mp3", "size_bytes": 16}
    assert plan["headers"] == {}  # xi-api-key deliberately redacted


def test_transcribe_dry_run_forwards_language(tmp_path):
    sample = tmp_path / "clip.wav"
    sample.write_bytes(b"abc")
    plan_path = transcribe(tmp_path / "out.json", sample, language="en", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["fields"] == {"model_id": "scribe_v2", "language_code": "en"}


def test_transcribe_dry_run_omits_language_when_not_given(tmp_path):
    sample = tmp_path / "clip.wav"
    sample.write_bytes(b"abc")
    plan_path = transcribe(tmp_path / "out.json", sample, dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert "language_code" not in plan["fields"]


def test_transcribe_dry_run_does_not_embed_audio_bytes(tmp_path):
    sample = tmp_path / "big.mp3"
    sample.write_bytes(b"\x00" * 5000)
    plan_path = transcribe(tmp_path / "out.json", sample, dry_run=True)
    text = plan_path.read_text()
    assert "\\x00" not in text
    assert len(text) < 1000  # a size number, not the raw 5000-byte payload


def test_transcribe_never_touches_network_or_auth_with_key_unset(tmp_path, monkeypatch):
    # Direct regression test for the exact bug class the module docstring warns
    # about (dry_run checked after headers/auth were built) — same shape as
    # test_elevenlabs_sfx.py's equivalent regression test.
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    sample = tmp_path / "interview.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    with mock.patch("urllib.request.urlopen") as urlopen:
        transcribe(tmp_path / "out.json", sample, dry_run=True)
        urlopen.assert_not_called()


# --------------------------------------------------------------------------- backend edge cases


def test_run_stt_nonexistent_path_raises_elevenlabs_error_not_oserror(tmp_path):
    # Must fire even in dry_run, since dry_run still needs to stat() the file
    # for its size — same posture as FishBackend.voice_clone.
    missing = tmp_path / "does-not-exist.mp3"
    req = TranscriptionRequest(source_audio_path=str(missing), dry_run=True)
    try:
        ElevenLabsBackend().run_stt(None, req)
    except ElevenLabsError as e:
        assert "does-not-exist.mp3" in str(e)
    else:
        raise AssertionError("expected ElevenLabsError, not a raw OSError, for a missing path")


def test_run_stt_dry_run_read_failure_after_is_file_check_raises_cleanly(tmp_path, monkeypatch):
    # The TOCTOU race the module docstring implies: a file that passes
    # is_file() but fails on stat() (permission change, deleted between check
    # and read) must raise a clean ElevenLabsError even on a dry run — the
    # exact bug class a sibling PR (speech_to_speech) shipped once already,
    # where only the real-run read was guarded, not the dry-run stat().
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    req = TranscriptionRequest(source_audio_path=str(sample), dry_run=True)

    import pathlib

    real_stat = pathlib.Path.stat
    calls = {"n": 0}

    def flaky_stat(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:  # first call is is_file()'s own internal stat()
            raise PermissionError("denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr("pathlib.Path.stat", flaky_stat)
    try:
        ElevenLabsBackend().run_stt(None, req)
    except ElevenLabsError as e:
        assert "couldn't read" in str(e)
    else:
        raise AssertionError("expected ElevenLabsError on a dry-run stat() failure after is_file() passed")


def test_run_stt_real_run_read_failure_after_is_file_check_raises_cleanly(tmp_path, monkeypatch):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    req = TranscriptionRequest(source_audio_path=str(sample), dry_run=False)

    def raise_permission_error(self):
        raise PermissionError("denied")

    monkeypatch.setattr("pathlib.Path.read_bytes", raise_permission_error)
    try:
        ElevenLabsBackend().run_stt(None, req)
    except ElevenLabsError as e:
        assert "couldn't read" in str(e)
    else:
        raise AssertionError("expected ElevenLabsError on a read failure after is_file() passed")


def test_run_stt_missing_api_key_raises_before_network(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    sample = tmp_path / "interview.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    req = TranscriptionRequest(source_audio_path=str(sample), dry_run=False)
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            ElevenLabsBackend().run_stt(None, req)
        except ElevenLabsError as e:
            assert "ELEVENLABS_API_KEY" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError when ELEVENLABS_API_KEY is unset")
        urlopen.assert_not_called()


# --------------------------------------------------------------------------- validate_op


def test_validate_op_accepts_stt_for_elevenlabs_stt():
    validate_op("elevenlabs-stt", "stt")  # must not raise


def test_validate_op_rejects_stt_for_every_other_audio_model():
    other_audio_models = [
        "atlas-tts-grok", "atlas-tts-elevenlabs-v3", "worder-tts", "fish-tts",
        "fish-voice-clone", "fish-voice-design", "elevenlabs-tts", "elevenlabs-sfx",
        "atlas-music-minimax",
    ]
    for sh in other_audio_models:
        try:
            validate_op(sh, "stt")
        except CapabilityError:
            pass
        else:
            raise AssertionError(f"expected CapabilityError: {sh} should not support stt")


def test_validate_op_rejects_tts_for_elevenlabs_stt():
    try:
        validate_op("elevenlabs-stt", "tts")
    except CapabilityError:
        pass
    else:
        raise AssertionError("elevenlabs-stt should not support tts")


def test_audio_cost_stt_unpriced():
    assert estimate_audio_cost("elevenlabs-stt") is None


# --------------------------------------------------------------------------- CLI


def test_cli_transcribe_dry_run(tmp_path):
    sample = tmp_path / "interview.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    r = CliRunner().invoke(
        cli, ["transcribe", str(sample), "-o", str(tmp_path / "out.json"), "--dry-run"],
    )
    assert r.exit_code == 0
    assert "📝" in r.output


def test_cli_transcribe_missing_source_errors(tmp_path):
    r = CliRunner().invoke(
        cli, ["transcribe", str(tmp_path / "nope.mp3"), "-o", str(tmp_path / "out.json")],
    )
    assert r.exit_code != 0  # click.Path(exists=True) rejects it before dispatch


def test_cli_transcribe_success_writes_json_and_prints_confirmation(tmp_path, monkeypatch):
    sample = tmp_path / "interview.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    out = tmp_path / "out.json"
    response = {"text": "hello world"}

    def fake_run_stt(self, resolved, req):
        return response

    monkeypatch.setattr(ElevenLabsBackend, "run_stt", fake_run_stt)
    r = CliRunner().invoke(cli, ["transcribe", str(sample), "-o", str(out)])
    assert r.exit_code == 0
    assert "✅" in r.output
    assert json.loads(out.read_text()) == response


def test_cli_transcribe_backend_error_is_clean_not_a_traceback(tmp_path, monkeypatch):
    # ElevenLabsError subclasses BackendError — the CLI's `transcribe` command
    # must catch it via the same `except BackendError` pattern as speak/sfx.
    def raise_elevenlabs_error(self, resolved, req):
        raise ElevenLabsError("ElevenLabs HTTP 401: invalid key")

    monkeypatch.setattr(ElevenLabsBackend, "run_stt", raise_elevenlabs_error)
    sample = tmp_path / "interview.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    r = CliRunner().invoke(cli, ["transcribe", str(sample), "-o", str(tmp_path / "out.json")])
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit)
    assert "❌" in r.output
    assert "invalid key" in r.output


# --------------------------------------------------------------------------- end-to-end (real HTTP dispatch, mocked urlopen)


def test_transcribe_success_writes_full_json_response(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    sample = tmp_path / "interview.mp3"
    sample.write_bytes(b"fake-audio-bytes")

    response = {
        "language_code": "en",
        "language_probability": 0.98,
        "text": "hello world",
        "words": [
            {"text": "hello", "start": 0.0, "end": 0.4, "type": "word", "speaker_id": None},
            {"text": "world", "start": 0.5, "end": 0.9, "type": "word", "speaker_id": None},
        ],
    }

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(response).encode()

    captured_requests = []

    def fake_urlopen(request, timeout=None):
        captured_requests.append(request)
        return _Resp()

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        out_path = transcribe(tmp_path / "out.json", sample, dry_run=False)

    assert out_path == tmp_path / "out.json"
    assert json.loads(out_path.read_text()) == response

    sent = captured_requests[0]
    assert sent.get_header("Xi-api-key") == "test-key"
    assert sent.full_url == "https://api.elevenlabs.io/v1/speech-to-text"
    # multipart body: the required model_id field and the uploaded file both
    # land in the raw body somewhere (hand-built multipart, not JSON).
    body = sent.data
    assert b'name="model_id"' in body
    assert b"scribe_v2" in body
    assert b'name="file"; filename="interview.mp3"' in body


def test_run_stt_response_missing_text_raises_instead_of_writing_garbage(monkeypatch, tmp_path):
    # A 2xx response with an unexpected shape (schema change, partial/async
    # envelope) must not be silently written out as if it were a valid
    # transcript.
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    sample = tmp_path / "interview.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    req = TranscriptionRequest(source_audio_path=str(sample), dry_run=False)

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"unexpected": "shape"}).encode()

    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        try:
            ElevenLabsBackend().run_stt(None, req)
        except ElevenLabsError as e:
            assert "text" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError when 'text' is missing from the response")


def test_transcribe_http_error_wraps_as_elevenlabs_error(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    sample = tmp_path / "interview.mp3"
    sample.write_bytes(b"fake-audio-bytes")

    def raise_422(req, timeout=None):
        raise _http_error(422, '{"detail": [{"loc": ["body", "model_id"], "msg": "field required"}]}')

    with mock.patch("urllib.request.urlopen", raise_422):
        try:
            transcribe(tmp_path / "out.json", sample, dry_run=False)
        except ElevenLabsError as e:
            assert "422" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError on HTTP 422")


def test_transcribe_persistent_429_raises_rate_limit_error(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    sample = tmp_path / "interview.mp3"
    sample.write_bytes(b"fake-audio-bytes")

    def raise_429(req, timeout=None):
        raise _http_error(429, "rate limited")

    with mock.patch("urllib.request.urlopen", raise_429):
        try:
            transcribe(tmp_path / "out.json", sample, dry_run=False)
        except ElevenLabsRateLimitError:
            pass
        else:
            raise AssertionError("expected ElevenLabsRateLimitError on persisted HTTP 429")
