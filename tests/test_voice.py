"""Voice orchestrator (nazca.voice) + CLI — issue #122 phase A2.

Covers clone_voice/design_voice dry-run dispatch, base64 decoding of voice_design
candidates, the CLI commands' dry-run/success paths, and — the gap the A1 review
caught — that validate_op actually REJECTS voice_clone/voice_design for every
other existing audio model, not just accepts them for the two new ones.
"""

from __future__ import annotations

import base64
import json

from click.testing import CliRunner

from nazca.capabilities import CapabilityError, validate_op
from nazca.cli import cli
from nazca.errors import AudioError
from nazca.voice import clone_voice, design_voice

# --------------------------------------------------------------------------- validate_op gate


def test_validate_op_accepts_voice_clone_for_fish_voice_clone():
    validate_op("fish-voice-clone", "voice_clone")  # must not raise


def test_validate_op_accepts_voice_design_for_fish_voice_design():
    validate_op("fish-voice-design", "voice_design")  # must not raise


def test_validate_op_rejects_voice_clone_for_every_other_audio_model():
    other_audio_models = ["fish-tts", "worder-tts", "atlas-tts-grok", "atlas-tts-elevenlabs-v3"]
    for sh in other_audio_models:
        try:
            validate_op(sh, "voice_clone")
        except CapabilityError:
            pass
        else:
            raise AssertionError(f"expected CapabilityError: {sh} should not support voice_clone")


def test_validate_op_rejects_voice_design_for_every_other_audio_model():
    other_audio_models = ["fish-tts", "worder-tts", "atlas-tts-grok", "atlas-tts-elevenlabs-v3"]
    for sh in other_audio_models:
        try:
            validate_op(sh, "voice_design")
        except CapabilityError:
            pass
        else:
            raise AssertionError(f"expected CapabilityError: {sh} should not support voice_design")


def test_validate_op_rejects_voice_clone_for_fish_voice_design_and_vice_versa():
    # The two new placeholders are each single-op — cross-rejection proves the ops
    # weren't accidentally unioned onto both entries.
    try:
        validate_op("fish-voice-design", "voice_clone")
    except CapabilityError:
        pass
    else:
        raise AssertionError("fish-voice-design should not support voice_clone")
    try:
        validate_op("fish-voice-clone", "voice_design")
    except CapabilityError:
        pass
    else:
        raise AssertionError("fish-voice-clone should not support voice_design")


# --------------------------------------------------------------------------- clone_voice


def test_clone_voice_dry_run_plan(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    plan = clone_voice("My Voice", [str(sample)], dry_run=True)
    assert plan["backend"] == "fish"
    assert plan["fields"]["title"] == "My Voice"


def test_clone_voice_default_model_is_fish_voice_clone(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    plan = clone_voice("My Voice", [str(sample)], dry_run=True)
    # No explicit --model given; resolves through the fish-voice-clone registry entry.
    assert plan["url"] == "https://api.fish.audio/model"


# --------------------------------------------------------------------------- design_voice


def test_design_voice_dry_run_plan_has_no_candidates_key():
    plan = design_voice("Warm, confident studio narrator", dry_run=True)
    assert plan["backend"] == "fish"
    assert "candidates" not in plan


def test_design_voice_decodes_audio_base64_to_bytes(monkeypatch):
    from nazca.backends.fish import FishBackend

    raw_audio = b"totally-not-mp3-but-stands-in"
    b64 = base64.b64encode(raw_audio).decode()

    def fake_voice_design(self, instruction, **kw):
        return {"candidates": [{"id": "c1", "index": 0, "audio_base64": b64, "sample_rate": 24000}]}

    monkeypatch.setattr(FishBackend, "voice_design", fake_voice_design)
    result = design_voice("A narrator voice", dry_run=False)
    assert result["candidates"][0]["audio_bytes"] == raw_audio
    assert "audio_base64" not in result["candidates"][0]
    assert result["candidates"][0]["id"] == "c1"


def test_design_voice_raises_on_candidate_missing_audio_base64(monkeypatch):
    # Fish's response shape is unverified against a live key — a candidate
    # without audio_base64 must raise, not silently become b"" (a 0-byte
    # "success" file with no indication anything went wrong).
    from nazca.backends.fish import FishBackend

    def fake_voice_design(self, instruction, **kw):
        return {"candidates": [{"id": "c1", "index": 0}]}  # no audio_base64

    monkeypatch.setattr(FishBackend, "voice_design", fake_voice_design)
    try:
        design_voice("A narrator voice", dry_run=False)
    except AudioError as e:
        assert "c1" in str(e)
    else:
        raise AssertionError("expected AudioError for a candidate with no audio_base64")


def test_design_voice_raises_on_malformed_audio_base64(monkeypatch):
    # base64.b64decode without validate=True silently discards invalid
    # characters instead of raising — must not be allowed to write garbage
    # bytes to disk as if they were valid audio.
    from nazca.backends.fish import FishBackend

    def fake_voice_design(self, instruction, **kw):
        return {"candidates": [{"id": "c1", "index": 0, "audio_base64": "not-valid-base64!!!"}]}

    monkeypatch.setattr(FishBackend, "voice_design", fake_voice_design)
    try:
        design_voice("A narrator voice", dry_run=False)
    except AudioError as e:
        assert "c1" in str(e)
    else:
        raise AssertionError("expected AudioError for malformed audio_base64")


def test_design_voice_raises_on_missing_candidates_key(monkeypatch):
    from nazca.backends.fish import FishBackend

    def fake_voice_design(self, instruction, **kw):
        return {"unexpected": "shape"}  # no "candidates" key at all

    monkeypatch.setattr(FishBackend, "voice_design", fake_voice_design)
    try:
        design_voice("A narrator voice", dry_run=False)
    except AudioError:
        pass
    else:
        raise AssertionError("expected AudioError when response has no 'candidates' key")


# --------------------------------------------------------------------------- CLI: voice-clone


def test_cli_voice_clone_dry_run(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")
    r = CliRunner().invoke(
        cli, ["voice-clone", str(sample), "--title", "My Voice", "--dry-run"],
    )
    assert r.exit_code == 0
    assert "📝" in r.output
    payload = r.output.split("📝", 1)[1].strip()
    plan = json.loads(payload)
    assert plan["fields"]["title"] == "My Voice"


def test_cli_voice_clone_requires_at_least_one_audio_file():
    r = CliRunner().invoke(cli, ["voice-clone", "--title", "My Voice", "--dry-run"])
    assert r.exit_code != 0


def test_cli_voice_clone_rejects_nonexistent_audio_path(tmp_path):
    missing = tmp_path / "does-not-exist.mp3"
    r = CliRunner().invoke(
        cli, ["voice-clone", str(missing), "--title", "My Voice", "--dry-run"],
    )
    assert r.exit_code != 0


def test_cli_voice_clone_backend_error_is_clean_not_a_traceback(tmp_path, monkeypatch):
    # FishError subclasses BackendError, not AudioError — catching only AudioError
    # (as the CLI originally did, copied from speak()) would let this propagate as
    # an unhandled exception instead of a clean "❌ ..." + exit 1.
    from nazca.backends.fish import FishBackend, FishError

    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")

    def raise_fish_error(self, title, audio_paths, **kw):
        raise FishError("Fish Audio HTTP 401: invalid key")

    monkeypatch.setattr(FishBackend, "voice_clone", raise_fish_error)
    r = CliRunner().invoke(cli, ["voice-clone", str(sample), "--title", "My Voice"])
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit)
    assert "❌" in r.output
    assert "invalid key" in r.output


def test_cli_voice_clone_success(tmp_path, monkeypatch):
    from nazca.backends.fish import FishBackend

    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"fake-audio-bytes")

    def fake_voice_clone(self, title, audio_paths, **kw):
        return {"_id": "ref_xyz", "title": title, "state": "trained"}

    monkeypatch.setattr(FishBackend, "voice_clone", fake_voice_clone)
    r = CliRunner().invoke(cli, ["voice-clone", str(sample), "--title", "My Voice"])
    assert r.exit_code == 0
    assert "ref_xyz" in r.output
    assert "nazca speak" in r.output


# --------------------------------------------------------------------------- CLI: voice-design


def test_cli_voice_design_dry_run_writes_sidecar(tmp_path):
    prefix = str(tmp_path / "narrator")
    r = CliRunner().invoke(
        cli, ["voice-design", "Warm, confident studio narrator", "-o", prefix, "--dry-run"],
    )
    assert r.exit_code == 0
    assert "📝" in r.output
    sidecar = tmp_path / "narrator.request.json"
    assert sidecar.exists()
    plan = json.loads(sidecar.read_text())
    assert plan["body"]["instruction"] == "Warm, confident studio narrator"


def test_cli_voice_design_backend_error_is_clean_not_a_traceback(tmp_path, monkeypatch):
    from nazca.backends.fish import FishBackend, FishError

    def raise_fish_error(self, instruction, **kw):
        raise FishError("Fish Audio HTTP 422: instruction too long")

    monkeypatch.setattr(FishBackend, "voice_design", raise_fish_error)
    prefix = str(tmp_path / "host")
    r = CliRunner().invoke(cli, ["voice-design", "Bright podcast host", "-o", prefix])
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit)
    assert "❌" in r.output
    assert "instruction too long" in r.output


def test_cli_voice_design_success_writes_numbered_files(tmp_path, monkeypatch):
    from nazca.backends.fish import FishBackend

    raw1 = base64.b64encode(b"audio-one").decode()
    raw2 = base64.b64encode(b"audio-two").decode()

    def fake_voice_design(self, instruction, **kw):
        return {
            "candidates": [
                {"id": "c0", "index": 0, "audio_base64": raw1},
                {"id": "c1", "index": 1, "audio_base64": raw2},
            ]
        }

    monkeypatch.setattr(FishBackend, "voice_design", fake_voice_design)
    prefix = str(tmp_path / "host")
    r = CliRunner().invoke(cli, ["voice-design", "Bright podcast host", "-o", prefix, "-n", "2"])
    assert r.exit_code == 0
    p0 = tmp_path / "host_0.mp3"
    p1 = tmp_path / "host_1.mp3"
    assert p0.read_bytes() == b"audio-one"
    assert p1.read_bytes() == b"audio-two"
    assert "c0" in r.output
    assert "c1" in r.output


def test_cli_voice_design_writes_distinct_files_when_response_omits_index(tmp_path, monkeypatch):
    # Fish's response shape is unverified against a live key. If `index` is
    # missing (or duplicated) on more than one candidate, the CLI must still
    # write N distinct files by loop position — not silently overwrite earlier
    # candidates while printing success for all of them.
    from nazca.backends.fish import FishBackend

    raw1 = base64.b64encode(b"audio-one").decode()
    raw2 = base64.b64encode(b"audio-two").decode()

    def fake_voice_design(self, instruction, **kw):
        return {
            "candidates": [
                {"id": "c0", "audio_base64": raw1},  # no "index" key
                {"id": "c1", "audio_base64": raw2},  # no "index" key
            ]
        }

    monkeypatch.setattr(FishBackend, "voice_design", fake_voice_design)
    prefix = str(tmp_path / "host")
    r = CliRunner().invoke(cli, ["voice-design", "Bright podcast host", "-o", prefix, "-n", "2"])
    assert r.exit_code == 0
    assert (tmp_path / "host_0.mp3").read_bytes() == b"audio-one"
    assert (tmp_path / "host_1.mp3").read_bytes() == b"audio-two"
