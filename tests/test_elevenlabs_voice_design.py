"""ElevenLabs voice design (issue #122 phase A3) — Step 1 of ElevenLabs' two-step
voice-creation flow (`POST /v1/text-to-voice/design`; Step 2, permanently
saving a chosen preview via `POST /v1/text-to-voice`, is deliberately out of
scope — see backends/elevenlabs.py's module docstring).

Mirrors tests/test_fish_audio.py's voice_design section (the closest existing
precedent for the dry-run/validation/success/error depth), adapted for
ElevenLabs' response reshaping (`previews` -> `candidates`, `generated_voice_id`
-> `id`, `audio_base_64` -> `audio_base64`) and lack of an `n`/`language`/`speed`
request-level knob, plus dry-run-never-touches-auth and validate_op coverage in
both directions, same as tests/test_elevenlabs_sfx.py.
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
from nazca.errors import AudioError
from nazca.voice import design_voice


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


def _clear_real_config_attr(name: str) -> None:
    import nazca.config as config

    config.__dict__.pop(name, None)


# --------------------------------------------------------------------------- dry-run plan


def test_voice_design_dry_run_plan():
    plan = ElevenLabsBackend().voice_design(
        "A sassy squeaky mouse with a Brooklyn accent", dry_run=True
    )
    assert plan["backend"] == "elevenlabs"
    assert plan["url"] == "https://api.elevenlabs.io/v1/text-to-voice/design"
    assert plan["body"] == {
        "voice_description": "A sassy squeaky mouse with a Brooklyn accent",
        "model_id": "eleven_multilingual_ttv_v2",
        "auto_generate_text": True,
    }
    # xi-api-key must never appear in a dry-run plan.
    assert "headers" in plan
    assert "xi-api-key" not in json.dumps(plan)


def test_voice_design_dry_run_plan_with_reference_text():
    plan = ElevenLabsBackend().voice_design(
        "Warm, confident studio narrator",
        reference_text="Every act of kindness leaves a ripple that never fades.",
        dry_run=True,
    )
    assert plan["body"]["text"] == "Every act of kindness leaves a ripple that never fades."
    assert "auto_generate_text" not in plan["body"]


def test_voice_design_ignores_n_language_speed_but_accepts_them(tmp_path):
    # nazca.voice.design_voice() calls backend.voice_design with n/language/speed
    # unconditionally for every backend (Fish uses them; ElevenLabs' /design has
    # no equivalent knob) — must not raise, and must not leak into the body.
    plan = ElevenLabsBackend().voice_design(
        "Bright upbeat podcast host", n=4, language="en", speed=1.2, dry_run=True
    )
    assert "n" not in plan["body"]
    assert "language" not in plan["body"]
    assert "speed" not in plan["body"]


def test_voice_design_empty_instruction_raises():
    try:
        ElevenLabsBackend().voice_design("", dry_run=True)
    except ElevenLabsError as e:
        assert "instruction" in str(e).lower()
    else:
        raise AssertionError("expected ElevenLabsError for empty instruction")


def test_voice_design_instruction_too_short_raises():
    try:
        ElevenLabsBackend().voice_design("short", dry_run=True)
    except ElevenLabsError as e:
        assert "20" in str(e)
    else:
        raise AssertionError("expected ElevenLabsError for a too-short instruction")


def test_voice_design_instruction_too_long_raises():
    try:
        ElevenLabsBackend().voice_design("x" * 1001, dry_run=True)
    except ElevenLabsError as e:
        assert "1000" in str(e)
    else:
        raise AssertionError("expected ElevenLabsError for a too-long instruction")


def test_voice_design_instruction_exactly_20_chars_is_accepted():
    # boundary check: len == 20 is the inclusive floor, must not raise
    plan = ElevenLabsBackend().voice_design("x" * 20, dry_run=True)
    assert plan["body"]["voice_description"] == "x" * 20


def test_voice_design_instruction_exactly_1000_chars_is_accepted():
    # boundary check: len == 1000 is the inclusive ceiling, must not raise
    plan = ElevenLabsBackend().voice_design("x" * 1000, dry_run=True)
    assert plan["body"]["voice_description"] == "x" * 1000


# --------------------------------------------------------------------------- auth


def test_voice_design_missing_api_key_raises_before_network(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            ElevenLabsBackend().voice_design("A narrator voice, warm and confident", dry_run=False)
        except ElevenLabsError as e:
            assert "ELEVENLABS_API_KEY" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError when ELEVENLABS_API_KEY is unset")
        urlopen.assert_not_called()


def test_dry_run_never_touches_auth_token_even_with_key_unset(monkeypatch):
    # Direct regression test for the exact bug class phase A2's voice_design
    # shipped (auth touched before the dry_run check).
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    with mock.patch("urllib.request.urlopen") as urlopen:
        plan = ElevenLabsBackend().voice_design("A narrator voice, warm and confident", dry_run=True)
        urlopen.assert_not_called()
    assert plan["backend"] == "elevenlabs"


# --------------------------------------------------------------------------- success/error


def test_voice_design_success_reshapes_previews_to_candidates(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(
                {
                    "previews": [
                        {
                            "generated_voice_id": "37HceQefKmEi3bGovXjL",
                            "audio_base_64": "ZmFrZQ==",
                            "media_type": "audio/mpeg",
                            "duration_secs": 4.2,
                            "language": "en",
                        },
                        {
                            "generated_voice_id": "anotherId456",
                            "audio_base_64": "ZmFrZTI=",
                            "media_type": "audio/mpeg",
                            "duration_secs": 3.9,
                            "language": "en",
                        },
                    ],
                    "text": "Every act of kindness leaves a ripple that never fades.",
                }
            ).encode()

    captured_requests = []

    def fake_urlopen(request, timeout=None):
        captured_requests.append(request)
        return _Resp()

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        out = ElevenLabsBackend().voice_design("A sassy squeaky mouse, Brooklyn accent", dry_run=False)

    assert list(out.keys()) == ["candidates"]
    assert len(out["candidates"]) == 2
    assert out["candidates"][0]["id"] == "37HceQefKmEi3bGovXjL"
    assert out["candidates"][0]["audio_base64"] == "ZmFrZQ=="
    assert "generated_voice_id" not in out["candidates"][0]
    assert "audio_base_64" not in out["candidates"][0]
    # Non-renamed preview fields pass through unchanged.
    assert out["candidates"][0]["media_type"] == "audio/mpeg"
    assert out["candidates"][0]["duration_secs"] == 4.2

    sent = captured_requests[0]
    assert sent.get_header("Xi-api-key") == "test-key"
    assert sent.full_url == "https://api.elevenlabs.io/v1/text-to-voice/design"


def test_voice_design_missing_previews_key_raises():
    _clear_real_config_attr("ELEVENLABS_API_KEY")

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
            with mock.patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test-key"}):
                ElevenLabsBackend().voice_design("A narrator voice, warm and confident", dry_run=False)
        except ElevenLabsError as e:
            assert "previews" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError when 'previews' is missing")


def test_voice_design_empty_previews_list_raises():
    # An empty list would pass a bare `is None` check and silently return zero
    # candidates (exit 0, no files written, no explanation) — must raise instead.
    _clear_real_config_attr("ELEVENLABS_API_KEY")

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"previews": [], "text": "sample"}).encode()

    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        try:
            with mock.patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test-key"}):
                ElevenLabsBackend().voice_design("A narrator voice, warm and confident", dry_run=False)
        except ElevenLabsError as e:
            assert "previews" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError for an empty previews list")


def test_voice_design_non_dict_preview_item_raises_cleanly():
    # A malformed response (previews present, but an item isn't an object) must
    # not crash with a raw TypeError from dict(preview) — a clean ElevenLabsError
    # instead, same "clean error, not a raw traceback" posture as the rest of
    # this backend.
    _clear_real_config_attr("ELEVENLABS_API_KEY")

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"previews": ["not-an-object"]}).encode()

    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        try:
            with mock.patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test-key"}):
                ElevenLabsBackend().voice_design("A narrator voice, warm and confident", dry_run=False)
        except ElevenLabsError as e:
            assert "preview" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError for a non-object preview item")


def test_voice_design_http_error_wraps_as_elevenlabs_error_with_hint(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")

    def raise_422(req, timeout=None):
        raise _http_error(
            422, '{"detail": [{"loc": ["body", "voice_description"], "msg": "String should have at '
            'least 20 characters", "type": "string_too_short"}]}'
        )

    with mock.patch("urllib.request.urlopen", raise_422):
        try:
            ElevenLabsBackend().voice_design("A narrator voice, warm and confident", dry_run=False)
        except ElevenLabsError as e:
            assert "422" in str(e)
            assert "validation" in str(e).lower()
        else:
            raise AssertionError("expected ElevenLabsError on HTTP 422")


def test_voice_design_persistent_429_raises_elevenlabs_rate_limit_error(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    monkeypatch.setenv("NAZCA_BACKOFF_BASE", "0")

    def raise_429(req, timeout=None):
        raise _http_error(429, '{"detail": {"status": "too_many_concurrent_requests"}}')

    with mock.patch("urllib.request.urlopen", raise_429):
        try:
            ElevenLabsBackend().voice_design("A narrator voice, warm and confident", dry_run=False)
        except ElevenLabsRateLimitError:
            pass
        else:
            raise AssertionError("expected ElevenLabsRateLimitError on persistent HTTP 429")


# --------------------------------------------------------------------------- orchestrator (nazca.voice)


def test_design_voice_orchestrator_dry_run_elevenlabs(tmp_path):
    result = design_voice(
        "A sassy squeaky mouse with a Brooklyn accent",
        model="elevenlabs-voice-design",
        dry_run=True,
    )
    assert result["backend"] == "elevenlabs"
    assert "candidates" not in result


def test_design_voice_orchestrator_decodes_audio_bytes_for_elevenlabs(monkeypatch):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(
                {
                    "previews": [
                        {
                            "generated_voice_id": "gvid1",
                            "audio_base_64": "ZmFrZQ==",
                            "media_type": "audio/mpeg",
                        }
                    ],
                    "text": "sample",
                }
            ).encode()

    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        out = design_voice(
            "A sassy squeaky mouse with a Brooklyn accent",
            model="elevenlabs-voice-design",
            dry_run=False,
        )
    # design_voice() (established by Fish) decodes audio_base64 -> audio_bytes
    # generically — must work unchanged for ElevenLabs' reshaped candidates too.
    assert out["candidates"][0]["audio_bytes"] == b"fake"
    assert out["candidates"][0]["id"] == "gvid1"


def test_design_voice_orchestrator_rejects_fish_only_n_for_elevenlabs():
    # Unlike the backend method (which silently ignores n/language/speed),
    # the orchestrator raises rather than letting a caller believe their
    # requested count took effect — same posture as clone_voice()'s
    # --visibility/--tags guard.
    try:
        design_voice(
            "A sassy squeaky mouse with a Brooklyn accent",
            model="elevenlabs-voice-design", n=4, dry_run=True,
        )
    except AudioError as e:
        assert "elevenlabs-voice-design" in str(e)
    else:
        raise AssertionError("expected AudioError for -n on a non-Fish backend")


def test_design_voice_orchestrator_rejects_fish_only_language_for_elevenlabs():
    try:
        design_voice(
            "A sassy squeaky mouse with a Brooklyn accent",
            model="elevenlabs-voice-design", language="en", dry_run=True,
        )
    except AudioError:
        pass
    else:
        raise AssertionError("expected AudioError for --language on a non-Fish backend")


def test_design_voice_orchestrator_rejects_fish_only_speed_for_elevenlabs():
    try:
        design_voice(
            "A sassy squeaky mouse with a Brooklyn accent",
            model="elevenlabs-voice-design", speed=1.5, dry_run=True,
        )
    except AudioError:
        pass
    else:
        raise AssertionError("expected AudioError for --speed on a non-Fish backend")


def test_cli_voice_design_rejects_language_flag_for_elevenlabs_cleanly():
    r = CliRunner().invoke(
        cli,
        [
            "voice-design", "A sassy squeaky mouse with a Brooklyn accent",
            "--model", "elevenlabs-voice-design", "--language", "en",
        ],
    )
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit)
    assert "❌" in r.output


def test_cli_voice_design_elevenlabs_dry_run(tmp_path):
    r = CliRunner().invoke(
        cli,
        [
            "voice-design", "A sassy squeaky mouse with a Brooklyn accent",
            "-o", str(tmp_path / "ev"), "--model", "elevenlabs-voice-design", "--dry-run",
        ],
    )
    assert r.exit_code == 0
    assert "📝" in r.output


# --------------------------------------------------------------------------- capabilities


def test_validate_op_accepts_voice_design_for_elevenlabs_voice_design():
    validate_op("elevenlabs-voice-design", "voice_design")  # must not raise


def test_validate_op_rejects_voice_design_for_every_other_audio_model():
    other_audio_models = [
        "atlas-tts-grok", "atlas-tts-elevenlabs-v3", "worder-tts", "fish-tts",
        "fish-voice-clone", "elevenlabs-tts", "elevenlabs-sfx", "atlas-music-minimax",
    ]
    for sh in other_audio_models:
        try:
            validate_op(sh, "voice_design")
        except CapabilityError:
            pass
        else:
            raise AssertionError(f"expected CapabilityError: {sh} should not support voice_design")


def test_validate_op_rejects_tts_for_elevenlabs_voice_design():
    try:
        validate_op("elevenlabs-voice-design", "tts")
    except CapabilityError:
        pass
    else:
        raise AssertionError("elevenlabs-voice-design should not support tts")


def test_audio_cost_voice_design_unpriced():
    assert estimate_audio_cost("elevenlabs-voice-design") is None
