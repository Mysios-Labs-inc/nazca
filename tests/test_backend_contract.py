"""Backend seam contract: every registered backend implements the run methods, and
a dry-run ImageRequest / VideoRequest round-trips to a plan dict without network.

This is the guard for the Open/Closed seam — adding a backend means implementing
`run_image` / `run_video`, never editing a dispatch ladder in image.py / video.py.
"""

from __future__ import annotations

import pytest
from PIL import Image

from nazca.backends import BACKENDS
from nazca.request import ImageRequest, VideoRequest


def _png(path):
    Image.new("RGB", (16, 16), (40, 80, 120)).save(path)
    return str(path)


# Resolved (model_id, api, region) routing per backend for the image dry-run probe.
_IMAGE_PROBE = {
    "vertex": ("gemini-2.5-flash-image", "gemini", "us-central1"),
    "fal": ("fal-ai/flux/schnell", "fal", ""),
    "modelark": ("seedream-4-0-250828", "modelark", ""),
    "openai": ("gpt-image-2", "openai", ""),
    "atlas": ("google/nano-banana-2", "atlas", ""),
}

# Resolved (model_id, region) routing per backend for the video dry-run probe.
# Only fal/modelark/vertex do video; the keys present here are the video-capable ones.
_VIDEO_PROBE = {
    "vertex": ("veo-3.1-generate-001", ""),
    "fal": ("fal-ai/wan/v2.6/text-to-video", ""),
    "modelark": ("bytedance-seedance-1-0-pro-250528", ""),
    "atlas": ("bytedance/seedance-2.0-mini", ""),
}


def test_every_backend_implements_run_methods():
    for name, backend in BACKENDS.items():
        assert callable(getattr(backend, "run_image", None)), f"{name}.run_image not callable"
        assert callable(getattr(backend, "run_video", None)), f"{name}.run_video not callable"


@pytest.mark.parametrize("name", list(BACKENDS))
def test_image_dry_run_round_trips_to_plan(name, tmp_path, monkeypatch):
    from nazca import config

    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")  # vertex build_url needs it
    backend = BACKENDS[name]
    model_id, api, region = _IMAGE_PROBE[name]
    req = ImageRequest(prompt="a test", refs=[], aspect_ratio="1:1", size="2K", dry_run=True)
    plan = backend.run_image(model_id, api, region, req)
    assert isinstance(plan, dict)
    # most backends echo model_id verbatim; atlas appends the op suffix to the slug stem
    assert plan["model"] == model_id or plan["model"].startswith(model_id + "/")
    assert not (tmp_path / "x.png").exists()  # dry-run writes nothing


@pytest.mark.parametrize("name", list(_VIDEO_PROBE))
def test_video_dry_run_round_trips_to_plan(name, monkeypatch):
    from nazca import config

    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")  # vertex build_url needs it
    backend = BACKENDS[name]
    model_id, region = _VIDEO_PROBE[name]
    req = VideoRequest(prompt="a clip", aspect_ratio="9:16", duration=8, dry_run=True)
    plan = backend.run_video(model_id, region, req)
    assert isinstance(plan, dict)
    assert "url" in plan
