"""Bounded exponential backoff for rate-limited provider POSTs (Tier 1, item A).

Vertex image gen is capped at 2 req/min per base model and `post()` historically
raised immediately on HTTP 429 — every caller (CLI, MCP, batch) inherited zero
resilience, so the retry had to be bolted on in external bash scripts. This module
ports that resilience into the backends so it lives in one place.

Retryable signals: HTTP 429, HTTP 503, a `RESOURCE_EXHAUSTED` status in the body,
or fal's `x-fal-needs-retry` requeue header. Delays grow geometrically from
`NAZCA_BACKOFF_BASE` seconds (default 20 → 20, 40, 80, 160, 320…), each jittered up
to +25% to avoid synchronized retry storms across lanes.

Tunables (env):
  NAZCA_MAX_RETRIES   retries after the first attempt (default 5; 0 = no retry,
                      which keeps the MCP path snappy).
  NAZCA_BACKOFF_BASE  first-delay seconds (default 20).
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from typing import Callable

RETRYABLE_STATUS = frozenset({429, 503})


def max_retries() -> int:
    """Retries after the first attempt. 0 disables retry entirely (snappy MCP path)."""
    try:
        return max(0, int(os.getenv("NAZCA_MAX_RETRIES", "5")))
    except ValueError:
        return 5


def backoff_base() -> float:
    """Seconds for the first backoff delay (subsequent delays double)."""
    try:
        return max(0.0, float(os.getenv("NAZCA_BACKOFF_BASE", "20")))
    except ValueError:
        return 20.0


def _is_retryable(code: int, body: str, headers: dict | None) -> bool:
    if code in RETRYABLE_STATUS:
        return True
    if "RESOURCE_EXHAUSTED" in body:
        return True
    # fal documents a server-side requeue via this header — honor it generically;
    # the header only appears on fal responses, so the check is a no-op elsewhere.
    if headers and str(headers.get("x-fal-needs-retry", "")).lower() == "true":
        return True
    return False


def post_json(
    url: str,
    body: dict,
    headers: dict,
    *,
    on_http_error: Callable[[int, str], Exception],
    on_rate_limited: Callable[[int, str], Exception],
    timeout: float | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _rand: Callable[[], float] = random.random,
) -> dict:
    """POST a JSON body with bounded exponential backoff, return decoded JSON.

    Retries up to `NAZCA_MAX_RETRIES` times on 429/503/RESOURCE_EXHAUSTED (and fal's
    requeue header). When retries are exhausted on a rate-limit error, raises the
    exception built by `on_rate_limited(code, detail)`; for any other HTTP error,
    raises `on_http_error(code, detail)`. Both callbacks receive the status code and
    the (truncated) response body so callers keep their own error types and messages.

    `_sleep`/`_rand` are injectable for testing.
    """
    data = json.dumps(body).encode()
    retries = max_retries()
    base = backoff_base()
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted provider endpoint)
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:600]
            retryable = _is_retryable(e.code, detail, dict(e.headers or {}))
            if retryable and attempt < retries:
                delay = base * (2 ** attempt)
                delay += delay * 0.25 * _rand()  # jitter: up to +25%
                _sleep(delay)
                continue
            if retryable:
                raise on_rate_limited(e.code, detail)
            raise on_http_error(e.code, detail)
    raise AssertionError("unreachable")  # loop always returns or raises
