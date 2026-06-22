"""Opt-in live smoke tests — deselected by default (pytest.ini addopts excludes 'live').

Run with:  pytest -m live

Each test is SKIPPED unless the relevant credential env var is present.  When a
key IS present the test makes one cheap, real API call and asserts:

  1. No exception was raised.
  2. The returned value is non-empty bytes (a valid image payload).

No assertions are made about image *content* — these tests only prove that the
provider ACCEPTS the request shape, not that the pixels look right.

Providers covered
-----------------
- openai:  OPENAI_API_KEY   → gpt-image-2 t2i, quality=low, 1024x1024
- fal:     FAL_KEY          → flux-schnell t2i, square
- modelark: ARK_API_KEY     → seedream t2i (cheapest size, 1K)
- vertex:  VERTEX_PROJECT   → nano-banana t2i via Gemini (gcloud ADC)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.live  # all tests in this module are 'live'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _has(var: str) -> bool:
    return bool(os.getenv(var))


def _require(*vars: str) -> None:
    """Skip the test when any required env var is absent."""
    missing = [v for v in vars if not os.getenv(v)]
    if missing:
        pytest.skip(f"env var(s) not set: {', '.join(missing)}")


# ---------------------------------------------------------------------------
# openai — gpt-image-2
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_live_openai_gpt_image_2():
    """Real call to /v1/images/generations — cheapest-possible config."""
    _require("OPENAI_API_KEY")

    from nazca.image import generate_image

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "smoke.png"
        result = generate_image(
            out,
            prompt="a solid red circle on white background",
            model="gpt-image-2",
            quality="low",
            aspect_ratio="1:1",  # → 1024x1024 (smallest square)
            dry_run=False,
        )
        assert result == out
        data = out.read_bytes()
        assert len(data) > 100, "image payload unexpectedly small"


# ---------------------------------------------------------------------------
# fal — flux-schnell
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has("FAL_KEY"), reason="FAL_KEY not set")
def test_live_fal_flux_schnell():
    """Real call to fal queue for flux-schnell — cheapest FLUX model."""
    _require("FAL_KEY")

    from nazca.image import generate_image

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "smoke.png"
        result = generate_image(
            out,
            prompt="a solid blue square on white background",
            model="flux-schnell",
            aspect_ratio="1:1",
            dry_run=False,
        )
        assert result == out
        data = out.read_bytes()
        assert len(data) > 100, "image payload unexpectedly small"


# ---------------------------------------------------------------------------
# modelark — seedream
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has("ARK_API_KEY"), reason="ARK_API_KEY not set")
def test_live_modelark_seedream():
    """Real call to ModelArk for Seedream 4.0 — cheapest size (1K)."""
    _require("ARK_API_KEY")

    from nazca.image import generate_image

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "smoke.png"
        result = generate_image(
            out,
            prompt="a solid green triangle on white background",
            model="seedream",
            aspect_ratio="1:1",
            size="1K",
            dry_run=False,
        )
        assert result == out
        data = out.read_bytes()
        assert len(data) > 100, "image payload unexpectedly small"


# ---------------------------------------------------------------------------
# vertex — nano-banana (Gemini image via gcloud ADC)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has("VERTEX_PROJECT"), reason="VERTEX_PROJECT not set")
def test_live_vertex_nano_banana():
    """Real call to Vertex AI Gemini image — gcloud ADC auth, default region.

    Requires:
      VERTEX_PROJECT  — GCP project that has Vertex AI enabled.
      Active gcloud ADC credentials (gcloud auth application-default login).
    """
    _require("VERTEX_PROJECT")

    # Also guard on gcloud being available; without it the token call will fail
    # in a way that is confusing and unrelated to API acceptance.
    import shutil

    if not shutil.which("gcloud"):
        # check common fallback paths used by the backend
        import os as _os
        fallbacks = (
            "~/google-cloud-sdk/bin/gcloud",
            "/opt/homebrew/bin/gcloud",
            "/usr/local/bin/gcloud",
        )
        found = any(
            Path(p).expanduser().is_file()
            for p in fallbacks
            if not _os.getenv("GCLOUD_BIN")
        )
        if not found:
            pytest.skip("gcloud binary not found on PATH or fallback locations")

    # Temporarily set the project so the backend picks it up from the env.
    orig = os.environ.get("VERTEX_PROJECT_ID")
    os.environ["VERTEX_PROJECT_ID"] = os.environ["VERTEX_PROJECT"]
    try:
        from nazca.image import generate_image

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "smoke.png"
            result = generate_image(
                out,
                prompt="a solid yellow star on white background",
                model="nano-banana",
                aspect_ratio="1:1",
                size="1K",
                dry_run=False,
            )
            assert result == out
            data = out.read_bytes()
            assert len(data) > 100, "image payload unexpectedly small"
    finally:
        if orig is None:
            os.environ.pop("VERTEX_PROJECT_ID", None)
        else:
            os.environ["VERTEX_PROJECT_ID"] = orig
