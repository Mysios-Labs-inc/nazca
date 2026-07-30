"""Fish Audio TTS (audio modality) — dry-run plan, missing-voice error, cost, error hints."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest import mock

from click.testing import CliRunner

from nazca.audio import speak
from nazca.backends.error_hints import hint
from nazca.backends.fish import FishBackend, FishError, FishRateLimitError
from nazca.cli import cli
from nazca.cost import estimate_audio_cost
from nazca.resolve import resolve


def test_speak_dry_run_plan_requires_voice(tmp_path):
    out = tmp_path / "hi.mp3"
    plan_path = speak(out, "Hello world", model="fish-tts", voice="ref_abc123", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["backend"] == "fish"
    assert plan["url"] == "https://api.fish.audio/v1/tts"
    assert plan["body"] == {
        "reference_id": "ref_abc123",
        "text": "Hello world",
        "format": "mp3",
    }
    assert plan["headers"] == {"model": "s2-pro"}


def test_speak_dry_run_plan_forwards_wav_format(tmp_path):
    out = tmp_path / "hi.wav"
    plan_path = speak(
        out, "Hello world", model="fish-tts", voice="ref_abc123",
        output_format="wav", dry_run=True,
    )
    plan = json.loads(plan_path.read_text())
    assert plan["body"]["format"] == "wav"


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


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


def _fish_request(voice="ref_abc123"):
    from nazca.request import AudioRequest

    resolved = resolve("fish-tts", "audio")
    req = AudioRequest(text="hi", voice=voice, dry_run=False)
    return resolved, req


def _clear_real_config_attr(name: str) -> None:
    """Remove a real module attribute if it exists, so config.__getattr__'s
    fresh env/ini resolution isn't shadowed by a stale value from a prior test.
    """
    import nazca.config as config

    config.__dict__.pop(name, None)


def test_run_audio_missing_api_key_raises_before_network(monkeypatch):
    _clear_real_config_attr("FISH_API_KEY")
    monkeypatch.delenv("FISH_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    resolved, req = _fish_request()
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            FishBackend().run_audio(resolved, req)
        except FishError as e:
            assert "FISH_API_KEY" in str(e)
        else:
            raise AssertionError("expected FishError when FISH_API_KEY is unset")
        urlopen.assert_not_called()


def test_run_audio_success_returns_raw_audio_bytes(monkeypatch):
    _clear_real_config_attr("FISH_API_KEY")
    monkeypatch.setenv("FISH_API_KEY", "test-key")
    resolved, req = _fish_request()
    audio_bytes = b"\xff\xd8\xff not real mp3 but stands in for one"

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return audio_bytes

    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        out = FishBackend().run_audio(resolved, req)
    assert out == audio_bytes


def test_run_audio_http_error_wraps_as_fish_error_with_hint(monkeypatch):
    _clear_real_config_attr("FISH_API_KEY")
    monkeypatch.setenv("FISH_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    resolved, req = _fish_request()

    def raise_401(req, timeout=None):
        raise _http_error(401, "Unauthorized")

    with mock.patch("urllib.request.urlopen", raise_401):
        try:
            FishBackend().run_audio(resolved, req)
        except FishError as e:
            assert "401" in str(e)
            assert "FISH_API_KEY" in str(e)
        else:
            raise AssertionError("expected FishError on HTTP 401")


def test_run_audio_persistent_429_raises_fish_rate_limit_error(monkeypatch):
    _clear_real_config_attr("FISH_API_KEY")
    monkeypatch.setenv("FISH_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    monkeypatch.setenv("NAZCA_BACKOFF_BASE", "0")
    resolved, req = _fish_request()

    def raise_429(req, timeout=None):
        raise _http_error(429, "rate limited")

    with mock.patch("urllib.request.urlopen", raise_429):
        try:
            FishBackend().run_audio(resolved, req)
        except FishRateLimitError:
            pass
        else:
            raise AssertionError("expected FishRateLimitError on persisted HTTP 429")
