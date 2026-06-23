"""Shared exception hierarchy for all nazca backends and orchestrators.

Every provider failure ultimately subclasses one of these two types, so call
sites can write a single ``except BackendError`` when they do not care which
provider failed, or ``except RateLimitError`` to distinguish a throttle event
from a genuine API error.

``ImageError`` and ``VeoError`` live here so backends can raise them without
importing the orchestrator modules (``image.py``, ``video.py``) — which would
create a circular import (backends ← image/video ← backends).
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


class VeoError(BackendError):
    """Raised for video-generation failures that are not provider-specific."""
