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
    """Minimal context-manager stand-in for a urllib response."""

    def __init__(self, payload: bytes):
        self._payload = payload

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


def test_fal_needs_retry_header(fast_retry):
    slept = fast_retry
    calls = {"n": 0}

    def needs_retry(req, timeout=None):
        calls["n"] += 1
        raise _http_error(202, "queued", {"x-fal-needs-retry": "true"})

    with mock.patch("urllib.request.urlopen", needs_retry):
        with pytest.raises(fal.FalRateLimitError):
            retry.post_json(
                "http://x",
                {},
                {},
                on_http_error=lambda c, d: RuntimeError("http"),
                on_rate_limited=lambda c, d: fal.FalRateLimitError("rl"),
                _sleep=slept.append,
                _rand=lambda: 0.0,
            )
    assert calls["n"] == 6


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


def test_modelark_rate_limit_type_is_distinct():
    # Subclass relationships let batch logic catch the base type or the specific one.
    assert issubclass(vertex.RateLimitError, vertex.VertexError)
    assert issubclass(fal.FalRateLimitError, fal.FalError)
    assert issubclass(modelark.ModelArkRateLimitError, modelark.ModelArkError)
