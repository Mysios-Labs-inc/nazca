"""Tests for nazca.retry — bounded backoff on rate-limited POSTs (item 1A)."""

from __future__ import annotations

import importlib
import io
import urllib.error
from unittest import mock

import pytest

from nazca import retry
from nazca.backends import fal, modelark, vertex


def _http_error(code: int, body: str = "", headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", headers or {}, io.BytesIO(body.encode()))


class _Resp:
    """Minimal context-manager stand-in for a urllib response (2xx success)."""

    def __init__(self, payload: bytes, status: int = 200, headers: dict | None = None):
        self._payload = payload
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


@pytest.fixture
def fast_retry(monkeypatch):
    """5 retries, base 20s, deterministic (no real sleep, no jitter)."""
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "5")
    monkeypatch.setenv("NAZCA_BACKOFF_BASE", "20")
    importlib.reload(retry)
    slept: list[float] = []
    yield slept
    importlib.reload(retry)  # restore module to env-default state


def _call(slept, urlopen):
    with mock.patch("urllib.request.urlopen", urlopen):
        return retry.post_json(
            "http://x",
            {},
            {},
            on_http_error=lambda c, d: RuntimeError(f"http {c}"),
            on_rate_limited=lambda c, d: vertex.RateLimitError(f"rl {c}"),
            _sleep=slept.append,
            _rand=lambda: 0.0,
        )


def test_persistent_429_exhausts_to_rate_limit_error(fast_retry):
    slept = fast_retry
    calls = {"n": 0}

    def always_429(req, timeout=None):
        calls["n"] += 1
        raise _http_error(429, "RESOURCE_EXHAUSTED quota")

    with pytest.raises(vertex.RateLimitError):
        _call(slept, always_429)

    assert calls["n"] == 6  # 1 initial + 5 retries
    assert slept == [20.0, 40.0, 80.0, 160.0, 320.0]  # geometric, no jitter


def test_retry_after_header_raises_backoff_floor(fast_retry):
    """A server `Retry-After: 90` overrides a shorter computed delay (20s → 90s)."""
    slept = fast_retry

    def with_retry_after(req, timeout=None):
        raise _http_error(429, "slow down", headers={"Retry-After": "90"})

    with pytest.raises(vertex.RateLimitError):
        _call(slept, with_retry_after)
    # First computed delay is 20s; Retry-After=90 is the floor, so we sleep 90.
    assert slept[0] == 90.0


def test_retry_after_smaller_than_backoff_is_ignored(fast_retry):
    """When the computed delay already exceeds Retry-After, keep the larger one."""
    slept = fast_retry

    def with_small_retry_after(req, timeout=None):
        raise _http_error(429, "slow down", headers={"Retry-After": "5"})

    with pytest.raises(vertex.RateLimitError):
        _call(slept, with_small_retry_after)
    assert slept == [20.0, 40.0, 80.0, 160.0, 320.0]  # Retry-After=5 never wins


def test_retry_after_unparseable_falls_back(fast_retry):
    """An HTTP-date (non-integer) Retry-After is ignored, not an error."""
    slept = fast_retry

    def http_date(req, timeout=None):
        raise _http_error(429, "x", headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})

    with pytest.raises(vertex.RateLimitError):
        _call(slept, http_date)
    assert slept == [20.0, 40.0, 80.0, 160.0, 320.0]


def test_resource_exhausted_in_body_is_retryable(fast_retry):
    slept = fast_retry
    calls = {"n": 0}

    def exhausted(req, timeout=None):
        calls["n"] += 1
        raise _http_error(400, "RESOURCE_EXHAUSTED: per-minute quota")

    with pytest.raises(vertex.RateLimitError):
        _call(slept, exhausted)
    assert calls["n"] == 6


def test_non_retryable_error_raises_immediately(fast_retry):
    slept = fast_retry
    calls = {"n": 0}

    def bad_request(req, timeout=None):
        calls["n"] += 1
        raise _http_error(400, "invalid argument")

    with pytest.raises(RuntimeError, match="http 400"):
        _call(slept, bad_request)
    assert calls["n"] == 1
    assert slept == []


def test_succeeds_after_transient_503(fast_retry):
    slept = fast_retry
    seq = [_http_error(503, "unavailable"), None]
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        item = seq.pop(0)
        if item is not None:
            raise item
        return _Resp(b'{"ok": true}')

    out = _call(slept, flaky)
    assert out == {"ok": True}
    assert calls["n"] == 2
    assert slept == [20.0]


def test_zero_retries_is_snappy(monkeypatch):
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "0")
    importlib.reload(retry)
    slept: list[float] = []
    calls = {"n": 0}

    def always_429(req, timeout=None):
        calls["n"] += 1
        raise _http_error(429, "quota")

    try:
        with pytest.raises(vertex.RateLimitError):
            _call(slept, always_429)
        assert calls["n"] == 1  # no retry
        assert slept == []
    finally:
        importlib.reload(retry)


def _post_fal(slept, urlopen):
    with mock.patch("urllib.request.urlopen", urlopen):
        return retry.post_json(
            "http://x",
            {},
            {},
            on_http_error=lambda c, d: RuntimeError("http"),
            on_rate_limited=lambda c, d: fal.FalRateLimitError("rl"),
            _sleep=slept.append,
            _rand=lambda: 0.0,
        )


def test_fal_needs_retry_header_on_2xx_success(fast_retry):
    # fal's requeue header rides on a *successful* response — urlopen does NOT
    # raise. Must be honored on the success path, not just on HTTP errors.
    slept = fast_retry
    calls = {"n": 0}

    def queued(req, timeout=None):
        calls["n"] += 1
        return _Resp(b'{"queued": true}', status=202, headers={"x-fal-needs-retry": "true"})

    with pytest.raises(fal.FalRateLimitError):
        _post_fal(slept, queued)
    assert calls["n"] == 6  # retried to exhaustion despite each call "succeeding"


def test_fal_needs_retry_header_then_success(fast_retry):
    slept = fast_retry
    seq = [
        _Resp(b'{"queued": true}', status=202, headers={"x-fal-needs-retry": "true"}),
        _Resp(b'{"ok": true}', status=200),
    ]

    def flaky(req, timeout=None):
        return seq.pop(0)

    out = _post_fal(slept, flaky)
    assert out == {"ok": True}
    assert slept == [20.0]


def test_jitter_adds_up_to_25_percent(fast_retry):
    slept = fast_retry

    def always_429(req, timeout=None):
        raise _http_error(429, "quota")

    with mock.patch("urllib.request.urlopen", always_429):
        with pytest.raises(vertex.RateLimitError):
            retry.post_json(
                "http://x",
                {},
                {},
                on_http_error=lambda c, d: RuntimeError("http"),
                on_rate_limited=lambda c, d: vertex.RateLimitError("rl"),
                _sleep=slept.append,
                _rand=lambda: 1.0,  # max jitter
            )
    # base*2**i * 1.25 at full jitter
    assert slept == [25.0, 50.0, 100.0, 200.0, 400.0]


def test_malformed_env_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("NAZCA_MAX_RETRIES", "not-a-number")
    monkeypatch.setenv("NAZCA_BACKOFF_BASE", "garbage")
    importlib.reload(retry)
    try:
        assert retry.max_retries() == 5
        assert retry.backoff_base() == 20.0
    finally:
        importlib.reload(retry)


def test_modelark_rate_limit_type_is_distinct():
    # Subclass relationships let batch logic catch the base type or the specific one.
    assert issubclass(vertex.RateLimitError, vertex.VertexError)
    assert issubclass(fal.FalRateLimitError, fal.FalError)
    assert issubclass(modelark.ModelArkRateLimitError, modelark.ModelArkError)
