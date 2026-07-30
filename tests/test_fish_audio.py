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


def test_cli_speak_backend_error_is_clean_not_a_traceback(tmp_path, monkeypatch):
    # FishError subclasses BackendError, not AudioError — the CLI's `speak` command
    # used to catch only AudioError, letting a real Fish HTTP error propagate as an
    # unhandled exception instead of a clean "❌ ..." + exit 1 (issue #122 A2 review).
    def raise_fish_error(self, resolved, req):
        raise FishError("Fish Audio HTTP 401: invalid key")

    monkeypatch.setattr(FishBackend, "run_audio", raise_fish_error)
    r = CliRunner().invoke(
        cli,
        [
            "speak", "Hello", "-o", str(tmp_path / "o.mp3"),
            "--model", "fish-tts", "--voice", "ref_abc123",
        ],
    )
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit)
    assert "❌" in r.output
    assert "invalid key" in r.output


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


# --------------------------------------------------------------------------- voice_clone


def test_voice_clone_dry_run_plan(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    plan = FishBackend().voice_clone("My Voice", [str(sample)], dry_run=True)
    assert plan["backend"] == "fish"
    assert plan["url"] == "https://api.fish.audio/model"
    assert plan["fields"]["title"] == "My Voice"
    assert plan["fields"]["type"] == "tts"
    assert plan["fields"]["train_mode"] == "fast"
    assert plan["fields"]["visibility"] == "private"  # nazca default, not Fish's "public"
    assert plan["files"] == [{"field": "voices", "filename": "sample.mp3", "size_bytes": 16}]


def test_voice_clone_dry_run_does_not_embed_audio_bytes(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"\x00" * 5000)
    plan = FishBackend().voice_clone("My Voice", [str(sample)], dry_run=True)
    assert json.dumps(plan).count("\\x00") == 0
    assert len(json.dumps(plan)) < 1000  # redacted, not the raw 5000-byte payload


def test_voice_clone_custom_options_in_dry_run_plan(tmp_path):
    s1 = tmp_path / "a.mp3"
    s2 = tmp_path / "b.mp3"
    s1.write_bytes(b"aaa")
    s2.write_bytes(b"bb")
    plan = FishBackend().voice_clone(
        "Narrator", [str(s1), str(s2)],
        description="A warm narrator voice", visibility="unlist", tags=["narration", "warm"],
        dry_run=True,
    )
    assert plan["fields"]["description"] == "A warm narrator voice"
    assert plan["fields"]["visibility"] == "unlist"
    assert plan["fields"]["tags"] == "narration,warm"
    assert len(plan["files"]) == 2


def test_voice_clone_empty_audio_paths_raises():
    try:
        FishBackend().voice_clone("My Voice", [], dry_run=True)
    except FishError as e:
        assert "audio" in str(e).lower()
    else:
        raise AssertionError("expected FishError for empty audio_paths")


def test_voice_clone_more_than_20_paths_raises():
    try:
        FishBackend().voice_clone("My Voice", [f"s{i}.mp3" for i in range(21)], dry_run=True)
    except FishError as e:
        assert "20" in str(e)
    else:
        raise AssertionError("expected FishError for >20 audio_paths")


def test_voice_clone_nonexistent_path_raises_fish_error_not_oserror(tmp_path):
    # A direct library caller of voice_clone() (bypassing the CLI's own
    # click.Path(exists=True) check) must get a clean FishError, not a raw
    # FileNotFoundError — this must fire even in dry_run, since dry_run still
    # needs to stat() each file for its size.
    missing = tmp_path / "does-not-exist.mp3"
    try:
        FishBackend().voice_clone("My Voice", [str(missing)], dry_run=True)
    except FishError as e:
        assert "does-not-exist.mp3" in str(e)
    else:
        raise AssertionError("expected FishError, not a raw OSError, for a missing path")


def test_voice_clone_missing_api_key_raises_before_network(monkeypatch, tmp_path):
    _clear_real_config_attr("FISH_API_KEY")
    monkeypatch.delenv("FISH_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            FishBackend().voice_clone("My Voice", [str(sample)], dry_run=False)
        except FishError as e:
            assert "FISH_API_KEY" in str(e)
        else:
            raise AssertionError("expected FishError when FISH_API_KEY is unset")
        urlopen.assert_not_called()


def test_voice_clone_success_returns_created_metadata(monkeypatch, tmp_path):
    _clear_real_config_attr("FISH_API_KEY")
    monkeypatch.setenv("FISH_API_KEY", "test-key")
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"_id": "ref_xyz", "title": "My Voice", "state": "trained"}).encode()

    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        out = FishBackend().voice_clone("My Voice", [str(sample)], dry_run=False)
    assert out == {"_id": "ref_xyz", "title": "My Voice", "state": "trained"}


def test_voice_clone_http_error_wraps_as_fish_error(monkeypatch, tmp_path):
    _clear_real_config_attr("FISH_API_KEY")
    monkeypatch.setenv("FISH_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")

    def raise_422(req, timeout=None):
        raise _http_error(422, '[{"loc": ["body", "title"], "type": "missing"}]')

    with mock.patch("urllib.request.urlopen", raise_422):
        try:
            FishBackend().voice_clone("My Voice", [str(sample)], dry_run=False)
        except FishError as e:
            assert "422" in str(e)
        else:
            raise AssertionError("expected FishError on HTTP 422")


# --------------------------------------------------------------------------- voice_design


def test_voice_design_dry_run_plan():
    plan = FishBackend().voice_design("Warm, confident studio narrator", dry_run=True)
    assert plan["backend"] == "fish"
    assert plan["url"] == "https://api.fish.audio/v1/voice-design"
    assert plan["body"] == {
        "instruction": "Warm, confident studio narrator",
        "n": 2,
        "speed": 1.0,
    }
    assert plan["headers"] == {"model": "voice-design-1"}


def test_voice_design_dry_run_plan_with_options():
    plan = FishBackend().voice_design(
        "Bright upbeat podcast host",
        reference_text="Hello and welcome to the show.",
        language="en",
        n=4,
        speed=1.2,
        dry_run=True,
    )
    assert plan["body"]["reference_text"] == "Hello and welcome to the show."
    assert plan["body"]["language"] == "en"
    assert plan["body"]["n"] == 4
    assert plan["body"]["speed"] == 1.2


def test_voice_design_empty_instruction_raises():
    try:
        FishBackend().voice_design("", dry_run=True)
    except FishError as e:
        assert "instruction" in str(e).lower()
    else:
        raise AssertionError("expected FishError for empty instruction")


def test_voice_design_n_out_of_range_raises():
    for bad_n in (0, 5):
        try:
            FishBackend().voice_design("A voice", n=bad_n, dry_run=True)
        except FishError as e:
            assert "n" in str(e).lower()
        else:
            raise AssertionError(f"expected FishError for n={bad_n}")


def test_voice_design_speed_out_of_range_raises():
    for bad_speed in (0, -1, 3.1):
        try:
            FishBackend().voice_design("A voice", speed=bad_speed, dry_run=True)
        except FishError as e:
            assert "speed" in str(e).lower()
        else:
            raise AssertionError(f"expected FishError for speed={bad_speed}")


def test_voice_design_missing_api_key_raises_before_network(monkeypatch):
    _clear_real_config_attr("FISH_API_KEY")
    monkeypatch.delenv("FISH_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            FishBackend().voice_design("A narrator voice", dry_run=False)
        except FishError as e:
            assert "FISH_API_KEY" in str(e)
        else:
            raise AssertionError("expected FishError when FISH_API_KEY is unset")
        urlopen.assert_not_called()


def test_voice_design_success_returns_candidates(monkeypatch):
    _clear_real_config_attr("FISH_API_KEY")
    monkeypatch.setenv("FISH_API_KEY", "test-key")

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(
                {"candidates": [{"id": "c1", "index": 0, "audio_base64": "ZmFrZQ==", "sample_rate": 24000}]}
            ).encode()

    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        out = FishBackend().voice_design("A narrator voice", dry_run=False)
    assert out["candidates"][0]["id"] == "c1"
    assert out["candidates"][0]["audio_base64"] == "ZmFrZQ=="


def test_voice_design_http_error_wraps_as_fish_error(monkeypatch):
    _clear_real_config_attr("FISH_API_KEY")
    monkeypatch.setenv("FISH_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")

    def raise_422(req, timeout=None):
        raise _http_error(422, '[{"loc": ["body", "instruction"], "type": "too_long"}]')

    with mock.patch("urllib.request.urlopen", raise_422):
        try:
            FishBackend().voice_design("x" * 3000, dry_run=False)
        except FishError as e:
            assert "422" in str(e)
        else:
            raise AssertionError("expected FishError on HTTP 422")
