"""ElevenLabs forced alignment (`align`: audio file + text -> timed
transcript) — issue #122 phase A3's third sub-phase.

Mirrors tests/test_elevenlabs_sfx.py's structure/depth for the third
ElevenLabs op: no --voice, multipart/form-data (like Fish's voice_clone, not
JSON like tts/sfx), a LOCAL source audio file as input, and JSON timing data
(not audio bytes) as the real-run output.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest import mock

from click.testing import CliRunner

from nazca.align import align_audio
from nazca.backends.elevenlabs import ElevenLabsBackend, ElevenLabsError, ElevenLabsRateLimitError
from nazca.capabilities import CapabilityError, validate_op
from nazca.cli import cli
from nazca.cost import estimate_audio_cost


def _sample(tmp_path, name="narration.mp3", content=b"fake audio bytes"):
    p = tmp_path / name
    p.write_bytes(content)
    return p


# --------------------------------------------------------------------------- dry-run plan


def test_align_audio_dry_run_plan(tmp_path):
    sample = _sample(tmp_path)
    plan_path = align_audio(tmp_path / "alignment.json", str(sample), "Hello, world.", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["backend"] == "elevenlabs"
    assert plan["url"] == "https://api.elevenlabs.io/v1/forced-alignment"
    assert plan["fields"] == {"text": "Hello, world."}
    assert plan["files"] == [{"field": "file", "filename": "narration.mp3", "size_bytes": len(b"fake audio bytes")}]
    assert plan["headers"] == {}  # xi-api-key deliberately redacted


def test_align_audio_dry_run_never_touches_auth_with_key_unset(tmp_path, monkeypatch):
    # Direct regression test for the exact bug class phase A2's voice_design
    # shipped (auth touched before the dry_run check) — see elevenlabs.py's
    # module docstring point 1.
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    sample = _sample(tmp_path)
    with mock.patch("urllib.request.urlopen") as urlopen:
        align_audio(tmp_path / "alignment.json", str(sample), "Hello, world.", dry_run=True)
        urlopen.assert_not_called()


def test_align_audio_dry_run_missing_source_file_raises_before_dry_run_plan(tmp_path):
    # backend.align() itself validates `source` even under dry_run (mirrors
    # Fish voice_clone's posture: a missing file is a clean error, not a
    # silently-empty plan).
    try:
        align_audio(tmp_path / "alignment.json", str(tmp_path / "missing.mp3"), "Hello.", dry_run=True)
    except ElevenLabsError as e:
        assert "missing.mp3" in str(e)
    else:
        raise AssertionError("expected ElevenLabsError for a missing source file")


def test_align_audio_default_model_is_elevenlabs_align(tmp_path):
    sample = _sample(tmp_path)
    plan_path = align_audio(tmp_path / "alignment.json", str(sample), "some text", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["url"] == "https://api.elevenlabs.io/v1/forced-alignment"


# --------------------------------------------------------------------------- capabilities


def test_validate_op_accepts_align_for_elevenlabs_align():
    validate_op("elevenlabs-align", "align")  # must not raise


def test_validate_op_rejects_align_for_every_other_audio_model():
    other_audio_models = [
        "atlas-tts-grok", "atlas-tts-elevenlabs-v3", "worder-tts", "fish-tts",
        "fish-voice-clone", "fish-voice-design", "elevenlabs-tts", "elevenlabs-sfx",
        "atlas-music-minimax",
    ]
    for sh in other_audio_models:
        try:
            validate_op(sh, "align")
        except CapabilityError:
            pass
        else:
            raise AssertionError(f"expected CapabilityError: {sh} should not support align")


def test_validate_op_rejects_tts_for_elevenlabs_align():
    try:
        validate_op("elevenlabs-align", "tts")
    except CapabilityError:
        pass
    else:
        raise AssertionError("elevenlabs-align should not support tts")


def test_audio_cost_align_unpriced():
    assert estimate_audio_cost("elevenlabs-align") is None


# --------------------------------------------------------------------------- CLI


def test_cli_align_dry_run(tmp_path):
    sample = _sample(tmp_path)
    r = CliRunner().invoke(
        cli,
        ["align", str(sample), "--text", "Hello, world.", "-o", str(tmp_path / "alignment.json"), "--dry-run"],
    )
    assert r.exit_code == 0
    assert "📝" in r.output


def test_cli_align_text_file(tmp_path):
    sample = _sample(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Hello from a file.")
    r = CliRunner().invoke(
        cli,
        ["align", str(sample), "--text-file", str(script), "-o", str(tmp_path / "alignment.json"), "--dry-run"],
    )
    assert r.exit_code == 0
    plan = json.loads((tmp_path / "alignment.request.json").read_text())
    assert plan["fields"] == {"text": "Hello from a file."}


def test_cli_align_requires_exactly_one_of_text_or_text_file(tmp_path):
    sample = _sample(tmp_path)
    r = CliRunner().invoke(cli, ["align", str(sample), "-o", str(tmp_path / "alignment.json"), "--dry-run"])
    assert r.exit_code == 2
    assert "❌" in r.output

    script = tmp_path / "script.txt"
    script.write_text("x")
    r = CliRunner().invoke(
        cli,
        [
            "align", str(sample), "--text", "a", "--text-file", str(script),
            "-o", str(tmp_path / "alignment.json"), "--dry-run",
        ],
    )
    assert r.exit_code == 2
    assert "❌" in r.output


def test_cli_align_missing_source_file_is_click_usage_error(tmp_path):
    r = CliRunner().invoke(
        cli,
        ["align", str(tmp_path / "missing.mp3"), "--text", "a", "-o", str(tmp_path / "alignment.json"), "--dry-run"],
    )
    assert r.exit_code != 0


def test_cli_align_backend_error_is_clean_not_a_traceback(tmp_path):
    # ElevenLabsError subclasses BackendError — the CLI's `align` command must
    # catch it via the same except BackendError pattern as speak/music/sfx.
    def raise_elevenlabs_error(self, source, text, **kwargs):
        raise ElevenLabsError("ElevenLabs HTTP 401: invalid key")

    sample = _sample(tmp_path)
    with mock.patch.object(ElevenLabsBackend, "align", raise_elevenlabs_error):
        r = CliRunner().invoke(cli, ["align", str(sample), "--text", "a", "-o", str(tmp_path / "alignment.json")])
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


def test_backend_align_success_returns_decoded_json(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    sample = _sample(tmp_path)

    response_body = json.dumps(
        {
            "characters": [{"text": "H", "start": 0.0, "end": 0.1}],
            "words": [{"text": "Hello", "start": 0.0, "end": 0.4, "loss": 0.01}],
            "loss": 0.01,
        }
    ).encode()

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return response_body

    captured_requests = []

    def fake_urlopen(request, timeout=None):
        captured_requests.append(request)
        return _Resp()

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        out = ElevenLabsBackend().align(str(sample), "Hello")
    assert out["words"][0]["text"] == "Hello"
    sent = captured_requests[0]
    assert sent.get_header("Xi-api-key") == "test-key"
    assert "/v1/forced-alignment" in sent.full_url
    assert sent.get_header("Content-type", "").startswith("multipart/form-data")
    body = sent.data
    assert b'name="text"' in body
    assert b"Hello" in body
    assert b'name="file"' in body
    assert b'filename="narration.mp3"' in body


def test_backend_align_missing_api_key_raises_before_network(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    sample = _sample(tmp_path)
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            ElevenLabsBackend().align(str(sample), "Hello")
        except ElevenLabsError as e:
            assert "ELEVENLABS_API_KEY" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError when ELEVENLABS_API_KEY is unset")
        urlopen.assert_not_called()


def test_backend_align_http_error_wraps_as_elevenlabs_error(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    sample = _sample(tmp_path)

    def raise_422(req, timeout=None):
        raise _http_error(422, '{"detail": [{"loc": ["body", "text"], "msg": "field required"}]}')

    with mock.patch("urllib.request.urlopen", raise_422):
        try:
            ElevenLabsBackend().align(str(sample), "Hello")
        except ElevenLabsError as e:
            assert "422" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError on HTTP 422")


def test_backend_align_persistent_429_raises_rate_limit_error(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    sample = _sample(tmp_path)

    def raise_429(req, timeout=None):
        raise _http_error(429, "rate limited")

    with mock.patch("urllib.request.urlopen", raise_429):
        try:
            ElevenLabsBackend().align(str(sample), "Hello")
        except ElevenLabsRateLimitError:
            pass
        else:
            raise AssertionError("expected ElevenLabsRateLimitError on persisted HTTP 429")


def test_align_audio_writes_json_result_not_bytes(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    sample = _sample(tmp_path)

    response_body = json.dumps({"characters": [], "words": [], "loss": 0.0}).encode()

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return response_body

    with mock.patch("urllib.request.urlopen", lambda request, timeout=None: _Resp()):
        out_path = align_audio(tmp_path / "alignment.json", str(sample), "Hello", dry_run=False)
    assert out_path == tmp_path / "alignment.json"
    written = json.loads(out_path.read_text())
    assert written == {"characters": [], "words": [], "loss": 0.0}
