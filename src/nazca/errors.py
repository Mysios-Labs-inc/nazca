"""Shared exception hierarchy for all nazca backends.

Every provider failure ultimately subclasses one of these two types, so call
sites can write a single ``except BackendError`` when they do not care which
provider failed, or ``except RateLimitError`` to distinguish a throttle event
from a genuine API error.
"""

from __future__ import annotations


class BackendError(RuntimeError):
    """Base class for all provider-level failures (HTTP errors, auth errors, etc.)."""


class RateLimitError(BackendError):
    """429/503/RESOURCE_EXHAUSTED that persisted past ``NAZCA_MAX_RETRIES`` retries.

    A distinct type so batch logic — and callers — can tell "paced wrong" from a
    real failure without inspecting the message text.
    """
