"""ElevenLabs voice cloning (`POST /v1/voices/add`) — issue #122 phase A3.

Mirrors tests/test_fish_audio.py's voice_clone section (the closest existing
precedent) for depth/structure: dry-run plan tests, a dry-run-never-touches-
auth regression test, validate_op tested in both directions, and end-to-end
tests exercising the real ElevenLabsBackend.voice_clone dispatch (mocked
urllib.request.urlopen) for a success path plus a representative error code.
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
from nazca.voice import clone_voice


def _clear_real_config_attr(name: str) -> None:
    """Remove a real module attribute if it exists, so config.__getattr__'s
    fresh env/ini resolution isn't shadowed by a stale value from a prior test.
    """
    import nazca.config as config

    config.__dict__.pop(name, None)


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


# --------------------------------------------------------------------------- dry-run plan


def test_voice_clone_dry_run_plan(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    plan = ElevenLabsBackend().voice_clone("My Voice", [str(sample)], dry_run=True)
    assert plan["backend"] == "elevenlabs"
    assert plan["url"] == "https://api.elevenlabs.io/v1/voices/add"
    assert plan["fields"] == {"name": "My Voice"}
    assert plan["files"] == [{"field": "files", "filename": "sample.mp3", "size_bytes": 16}]


def test_voice_clone_dry_run_does_not_embed_audio_bytes(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"\x00" * 5000)
    plan = ElevenLabsBackend().voice_clone("My Voice", [str(sample)], dry_run=True)
    assert json.dumps(plan).count("\\x00") == 0
    assert len(json.dumps(plan)) < 1000  # redacted, not the raw 5000-byte payload


def test_voice_clone_custom_options_in_dry_run_plan(tmp_path):
    s1 = tmp_path / "a.mp3"
    s2 = tmp_path / "b.mp3"
    s1.write_bytes(b"aaa")
    s2.write_bytes(b"bb")
    plan = ElevenLabsBackend().voice_clone(
        "Narrator", [str(s1), str(s2)],
        description="A warm narrator voice", remove_background_noise=True,
        dry_run=True,
    )
    assert plan["fields"]["description"] == "A warm narrator voice"
    assert plan["fields"]["remove_background_noise"] == "true"
    assert len(plan["files"]) == 2


def test_voice_clone_ignores_fish_only_visibility_and_tags(tmp_path):
    # ElevenLabs has no equivalent of Fish's visibility/tags fields — they must
    # be accepted (so this method satisfies nazca.voice.clone_voice()'s uniform
    # call shape across backends) but never forwarded to ElevenLabs' API.
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    plan = ElevenLabsBackend().voice_clone(
        "My Voice", [str(sample)], visibility="public", tags=["narration"], dry_run=True,
    )
    assert plan["fields"] == {"name": "My Voice"}
    assert "visibility" not in plan["fields"]
    assert "tags" not in plan["fields"]


def test_voice_clone_no_per_call_sample_count_cap(tmp_path):
    # Unlike Fish (caps at 20/call), ElevenLabs' spec documents no per-call
    # limit — only a workspace-wide 500-total-voices cap nazca can't check
    # client-side. 25 samples must not raise.
    paths = []
    for i in range(25):
        p = tmp_path / f"s{i}.mp3"
        p.write_bytes(b"x")
        paths.append(str(p))
    plan = ElevenLabsBackend().voice_clone("Many Samples", paths, dry_run=True)
    assert len(plan["files"]) == 25


def test_voice_clone_empty_audio_paths_raises():
    try:
        ElevenLabsBackend().voice_clone("My Voice", [], dry_run=True)
    except ElevenLabsError as e:
        assert "audio" in str(e).lower()
    else:
        raise AssertionError("expected ElevenLabsError for empty audio_paths")


def test_voice_clone_nonexistent_path_raises_elevenlabs_error_not_oserror(tmp_path):
    # A direct library caller of voice_clone() (bypassing the CLI's own
    # click.Path(exists=True) check) must get a clean ElevenLabsError, not a
    # raw FileNotFoundError — this must fire even in dry_run, since dry_run
    # still needs to stat() each file for its size.
    missing = tmp_path / "does-not-exist.mp3"
    try:
        ElevenLabsBackend().voice_clone("My Voice", [str(missing)], dry_run=True)
    except ElevenLabsError as e:
        assert "does-not-exist.mp3" in str(e)
    else:
        raise AssertionError("expected ElevenLabsError, not a raw OSError, for a missing path")


# --------------------------------------------------------------------------- dry-run never touches auth


def test_voice_clone_dry_run_never_touches_auth_or_network(monkeypatch, tmp_path):
    # Direct regression test for the exact bug class phase A2's voice_design
    # shipped (auth touched before the dry_run check, caught by CI's clean
    # env but not local dev's cached key) — this is THE invariant for new
    # ElevenLabs methods.
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    with mock.patch("urllib.request.urlopen") as urlopen:
        ElevenLabsBackend().voice_clone("My Voice", [str(sample)], dry_run=True)
        urlopen.assert_not_called()


def test_voice_clone_missing_api_key_raises_before_network(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    with mock.patch("urllib.request.urlopen") as urlopen:
        try:
            ElevenLabsBackend().voice_clone("My Voice", [str(sample)], dry_run=False)
        except ElevenLabsError as e:
            assert "ELEVENLABS_API_KEY" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError when ELEVENLABS_API_KEY is unset")
        urlopen.assert_not_called()


# --------------------------------------------------------------------------- validate_op


def test_validate_op_accepts_voice_clone_for_elevenlabs_voice_clone():
    validate_op("elevenlabs-voice-clone", "voice_clone")  # must not raise


def test_validate_op_rejects_voice_clone_for_every_other_audio_model():
    other_audio_models = [
        "atlas-tts-grok", "atlas-tts-elevenlabs-v3", "worder-tts", "fish-tts",
        "fish-voice-design", "elevenlabs-tts", "elevenlabs-sfx", "atlas-music-minimax",
    ]
    for sh in other_audio_models:
        try:
            validate_op(sh, "voice_clone")
        except CapabilityError:
            pass
        else:
            raise AssertionError(f"expected CapabilityError: {sh} should not support voice_clone")


def test_validate_op_rejects_tts_for_elevenlabs_voice_clone():
    try:
        validate_op("elevenlabs-voice-clone", "tts")
    except CapabilityError:
        pass
    else:
        raise AssertionError("elevenlabs-voice-clone should not support tts")


# --------------------------------------------------------------------------- orchestrator (nazca.voice.clone_voice)


def test_clone_voice_orchestrator_dispatches_to_elevenlabs(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    plan = clone_voice(
        "My Voice", [str(sample)], model="elevenlabs-voice-clone", dry_run=True,
    )
    assert plan["backend"] == "elevenlabs"
    assert plan["url"] == "https://api.elevenlabs.io/v1/voices/add"


# --------------------------------------------------------------------------- CLI


def test_cli_voice_clone_elevenlabs_dry_run(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    r = CliRunner().invoke(
        cli,
        [
            "voice-clone", str(sample), "--title", "My Voice",
            "--model", "elevenlabs-voice-clone", "--dry-run",
        ],
    )
    assert r.exit_code == 0
    assert "📝" in r.output
    plan = json.loads(r.output.split("📝 ", 1)[1])
    assert plan["backend"] == "elevenlabs"


def test_cli_voice_clone_backend_error_is_clean_not_a_traceback(tmp_path, monkeypatch):
    def raise_elevenlabs_error(self, title, audio_paths, **kwargs):
        raise ElevenLabsError("ElevenLabs HTTP 401: invalid key")

    monkeypatch.setattr(ElevenLabsBackend, "voice_clone", raise_elevenlabs_error)
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    r = CliRunner().invoke(
        cli,
        ["voice-clone", str(sample), "--title", "My Voice", "--model", "elevenlabs-voice-clone"],
    )
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit)
    assert "❌" in r.output
    assert "invalid key" in r.output


# --------------------------------------------------------------------------- end-to-end (real HTTP dispatch, mocked urlopen)


def test_voice_clone_success_returns_created_metadata(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")

    class _Resp:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"voice_id": "c38kUX8pkfYO2kHyqfFy", "requires_verification": False}).encode()

    captured_requests = []

    def fake_urlopen(request, timeout=None):
        captured_requests.append(request)
        return _Resp()

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        out = ElevenLabsBackend().voice_clone("My Voice", [str(sample)], dry_run=False)
    assert out == {"voice_id": "c38kUX8pkfYO2kHyqfFy", "requires_verification": False}
    sent = captured_requests[0]
    assert sent.get_header("Xi-api-key") == "test-key"
    assert sent.full_url == "https://api.elevenlabs.io/v1/voices/add"
    assert b'name="name"' in sent.data
    assert b"My Voice" in sent.data
    assert b'name="files"; filename="sample.mp3"' in sent.data


def test_voice_clone_http_error_wraps_as_elevenlabs_error(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")

    def raise_422(req, timeout=None):
        raise _http_error(422, '{"detail": [{"loc": ["body", "name"], "msg": "field required"}]}')

    with mock.patch("urllib.request.urlopen", raise_422):
        try:
            ElevenLabsBackend().voice_clone("My Voice", [str(sample)], dry_run=False)
        except ElevenLabsError as e:
            assert "422" in str(e)
        else:
            raise AssertionError("expected ElevenLabsError on HTTP 422")


def test_voice_clone_persistent_429_raises_rate_limit_error(monkeypatch, tmp_path):
    _clear_real_config_attr("ELEVENLABS_API_KEY")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")

    def raise_429(req, timeout=None):
        raise _http_error(429, "rate limited")

    with mock.patch("urllib.request.urlopen", raise_429):
        try:
            ElevenLabsBackend().voice_clone("My Voice", [str(sample)], dry_run=False)
        except ElevenLabsRateLimitError:
            pass
        else:
            raise AssertionError("expected ElevenLabsRateLimitError on persisted HTTP 429")
