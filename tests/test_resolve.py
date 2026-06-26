"""Parity tests for nazca.resolve.resolve() against the legacy resolver contract.

The real parity guard is the block of **hardcoded literal-tuple assertions**
below: each pins the EXACT tuple the pre-refactor ``_resolve*`` produced for a
given input class (prefix passthrough, override, registry hit, unknown fallback,
raise-on-unknown). Those literals are the frozen spec — they would fail if
``resolve()`` drifted.

``_assert_parity`` additionally checks that the public ``_resolve*`` adapters and
``resolve()`` agree. NOTE: this is a *delegation-consistency* check, not an
independent oracle — the adapters now delegate to ``resolve()``, so this catches
a broken adapter wiring, while the literal assertions catch logic drift.

  image  → (provider_id, region, api, backend)   [4-tuple]
  video  → (backend, provider_id)                [2-tuple]
  audio  → (backend, provider_id)                [2-tuple]
  3d     → (backend, provider_id)                [2-tuple]
"""

from __future__ import annotations

import json

import pytest

from nazca import config
from nazca.audio import AudioError
from nazca.audio import _resolve_audio as old_audio
from nazca.image import _resolve as old_image
from nazca.resolve import ResolvedModel, resolve
from nazca.threed import ThreeDError
from nazca.threed import _resolve_3d as old_3d
from nazca.video import _resolve_video as old_video


# --------------------------------------------------------------------------- helpers
def _old(modality: str, model):
    if modality == "image":
        return old_image(model)
    if modality == "video":
        return old_video(model)
    if modality == "audio":
        return old_audio(model)
    if modality == "3d":
        return old_3d(model)
    raise AssertionError(modality)


def _as_tuple(r: ResolvedModel, modality: str):
    """Reconstruct the legacy tuple shape from a ResolvedModel."""
    if modality == "image":
        return (r.provider_id, r.region, r.api, r.backend)
    return (r.backend, r.provider_id)


def _assert_parity(modality: str, model: str):
    """resolve() reconstructs the EXACT tuple the old resolver returns."""
    new = resolve(model, modality)
    assert _as_tuple(new, modality) == _old(modality, model), (modality, model)
    return new


# --------------------------------------------------------------------------- (a) built-ins
_BUILTINS = [
    ("image", "nano-banana"),
    ("image", "nano-banana-pro"),
    ("image", "imagen-4-fast"),
    ("image", "seedream"),
    ("image", "gpt-image-2"),
    ("image", "atlas-flux-2-pro"),
    ("video", "veo-3.1"),
    ("video", "veo-3.1-lite"),
    ("video", "seedance-pro"),   # modelark
    ("video", "seedance-2-fast"),  # fal
    ("video", "wan-2.6"),        # fal
    ("video", "atlas-veo-3.1"),  # atlas
    ("audio", "atlas-tts-grok"),
    ("audio", "atlas-tts-elevenlabs-v3"),
    ("3d", "atlas-hunyuan3d-rapid"),
    ("3d", "atlas-hunyuan3d-pro"),
    ("3d", "atlas-seed3d-2"),
]


@pytest.mark.parametrize(("modality", "model"), _BUILTINS)
def test_builtin_parity(modality, model):
    new = _assert_parity(modality, model)
    # built-in resolution carries the canonical spec for that shorthand
    assert new.spec is not None
    assert new.spec.shorthand == model


# --------------------------------------------------------------------------- (b) prefixes
_PREFIXES = [
    ("image", "vertex:my-raw-id"),
    ("image", "veo:my-raw-id"),
    ("image", "fal:fal-ai/whatever"),
    ("image", "ark:seedream-x"),
    ("image", "modelark:seedream-x"),
    ("image", "openai:gpt-image-9"),
    ("image", "oai:gpt-image-9"),
    ("image", "atlas:google/some-model"),
    ("video", "vertex:veo-raw"),
    ("video", "veo:veo-raw"),
    ("video", "fal:fal-ai/video"),
    ("video", "ark:seedance-raw"),
    ("video", "modelark:seedance-raw"),
    ("video", "atlas:google/veo-raw"),
    ("audio", "atlas:xai/tts-raw"),
    ("3d", "atlas:tencent/hunyuan-raw"),
]


@pytest.mark.parametrize(("modality", "model"), _PREFIXES)
def test_prefix_parity(modality, model):
    new = _assert_parity(modality, model)
    # prefix-passthrough has no registry spec
    assert new.spec is None
    # raw id is preserved verbatim (everything after the first colon)
    assert new.provider_id == model.split(":", 1)[1]


def test_image_prefix_region_api_exact():
    """The image prefix arm fills region/api precisely (the others are '')."""
    assert resolve("vertex:x", "image").region == "us-central1"
    assert resolve("vertex:x", "image").api == "gemini"
    assert resolve("veo:x", "image").api == "gemini"
    assert resolve("fal:x", "image").region == ""
    assert resolve("fal:x", "image").api == "fal"
    assert resolve("modelark:x", "image").api == "modelark"
    assert resolve("openai:x", "image").api == "openai"
    assert resolve("atlas:x", "image").api == "atlas"


def test_video_prefix_drops_openai():
    """video's prefix table has NO openai/oai arm — 'openai:foo' is NOT a prefix
    hit; it falls through to the vertex raw-id fallback, exactly like the old code."""
    assert resolve("openai:foo", "video") == ResolvedModel(
        "openai:foo", "openai:foo", "vertex", "", "", None
    )
    assert old_video("openai:foo") == ("vertex", "openai:foo")


def test_audio_prefix_non_atlas_raises():
    """audio honors only the atlas prefix; 'fal:x' is not a prefix → unknown → raise."""
    with pytest.raises(AudioError):
        resolve("fal:x", "audio")
    with pytest.raises(AudioError):
        old_audio("fal:x")


# --------------------------------------------------------------------------- (c) overrides
def _write_overrides(tmp_path, monkeypatch, payload):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = tmp_path / "nazca"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "models.json").write_text(json.dumps(payload))


def test_image_override_parity(tmp_path, monkeypatch):
    _write_overrides(
        tmp_path,
        monkeypatch,
        {
            "image": {
                "myimg": {
                    "id": "custom/raw-image",
                    "backend": "modelark",
                    "api": "modelark",
                    "region": "",
                }
            }
        },
    )
    new = _assert_parity("image", "myimg")
    assert _as_tuple(new, "image") == ("custom/raw-image", "", "modelark", "modelark")
    assert new.spec is None  # overrides have no registry spec


def test_image_override_defaults_parity(tmp_path, monkeypatch):
    """A sparse override entry falls back to the documented vertex/gemini defaults."""
    _write_overrides(tmp_path, monkeypatch, {"image": {"sparse": {}}})
    new = _assert_parity("image", "sparse")
    assert _as_tuple(new, "image") == ("sparse", "us-central1", "gemini", "vertex")


def test_video_override_parity(tmp_path, monkeypatch):
    _write_overrides(
        tmp_path,
        monkeypatch,
        {"video": {"myvid": {"id": "fal-ai/custom-video", "backend": "fal"}}},
    )
    new = _assert_parity("video", "myvid")
    assert _as_tuple(new, "video") == ("fal", "fal-ai/custom-video")
    assert new.spec is None


def test_video_override_vertex_fallback_parity(tmp_path, monkeypatch):
    """A non-(fal/modelark/atlas) video override backend collapses to vertex."""
    _write_overrides(
        tmp_path,
        monkeypatch,
        {"video": {"myvid": {"id": "veo-custom", "backend": "something-else"}}},
    )
    new = _assert_parity("video", "myvid")
    assert _as_tuple(new, "video") == ("vertex", "veo-custom")


# --------------------------------------------------------------------------- (d) fallback / raise
def test_image_unknown_fallback_parity():
    new = _assert_parity("image", "totally-unknown-xyz")
    assert _as_tuple(new, "image") == ("totally-unknown-xyz", "us-central1", "gemini", "vertex")
    assert new.spec is None


def test_video_unknown_fallback_parity():
    new = _assert_parity("video", "totally-unknown-xyz")
    assert _as_tuple(new, "video") == ("vertex", "totally-unknown-xyz")
    assert new.spec is None


def test_audio_unknown_raises_parity():
    with pytest.raises(AudioError) as new_exc:
        resolve("totally-unknown-xyz", "audio")
    with pytest.raises(AudioError) as old_exc:
        old_audio("totally-unknown-xyz")
    assert str(new_exc.value) == str(old_exc.value)


def test_3d_unknown_raises_parity():
    with pytest.raises(ThreeDError) as new_exc:
        resolve("totally-unknown-xyz", "3d")
    with pytest.raises(ThreeDError) as old_exc:
        old_3d("totally-unknown-xyz")
    assert str(new_exc.value) == str(old_exc.value)


# --------------------------------------------------------------------------- None defaults
def test_none_defaults_parity(monkeypatch):
    # image / audio / 3d apply their own default inside the old resolver
    assert _as_tuple(resolve(None, "image"), "image") == old_image(None)
    assert _as_tuple(resolve(None, "audio"), "audio") == old_audio(None)
    assert _as_tuple(resolve(None, "3d"), "3d") == old_3d(None)
    # video's default is config.VEO_MODEL (applied by the orchestrator, not _resolve_video)
    assert _as_tuple(resolve(None, "video"), "video") == old_video(config.VEO_MODEL)
