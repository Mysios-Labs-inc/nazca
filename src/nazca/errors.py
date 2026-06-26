"""Shared exception hierarchy for all nazca backends and orchestrators.

Every provider failure ultimately subclasses one of these two types, so call
sites can write a single ``except BackendError`` when they do not care which
provider failed, or ``except RateLimitError`` to distinguish a throttle event
from a genuine API error.

All modality errors (``ImageError``, ``VideoError``, ``AudioError``,
``ThreeDError``) live here so backends can raise them without importing the
orchestrator modules (``image.py``, ``video.py``, …) — which would create a
circular import (backends ← orchestrator ← backends). ``VeoError`` is a
backward-compatible alias of ``VideoError``.
"""

from __future__ import annotations


class BackendError(RuntimeError):
    """Base class for all provider-level failures (HTTP errors, auth errors, etc.)."""


class RateLimitError(BackendError):
    """429/503/RESOURCE_EXHAUSTED that persisted past ``NAZCA_MAX_RETRIES`` retries.

    A distinct type so batch logic — and callers — can tell "paced wrong" from a
    real failure without inspecting the message text.
    """


class ImageError(BackendError):
    """Raised for image-generation failures that are not provider-specific."""


class VideoError(BackendError):
    """Raised for video-generation failures that are not provider-specific."""


VeoError = VideoError
"""Alias for backward compatibility; video errors use VideoError."""


class AudioError(BackendError):
    """Raised when audio synthesis fails or no audio model is resolvable."""


class ThreeDError(BackendError):
    """Raised when 3D generation fails or no 3D model is resolvable."""
