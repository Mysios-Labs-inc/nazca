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


def _call_bytes(slept, urlopen):
    with mock.patch("urllib.request.urlopen", urlopen):
        return retry.post_bytes(
            "http://x",
            {},
            {},
            on_http_error=lambda c, d: RuntimeError(f"http {c}"),
            on_rate_limited=lambda c, d: vertex.RateLimitError(f"rl {c}"),
            _sleep=slept.append,
            _rand=lambda: 0.0,
        )


def test_post_bytes_returns_raw_body_unparsed(fast_retry):
    """`post_bytes` must hand back the raw bytes, never json.loads them — a body
    that isn't valid JSON (like an audio stream) would otherwise raise here.
    """
    slept = fast_retry
    not_json = b"\xff\xd8\xff not json audio bytes"
    out = _call_bytes(slept, lambda req, timeout=None: _Resp(not_json))
    assert out == not_json
    assert isinstance(out, bytes)


def test_post_bytes_persistent_429_exhausts_to_rate_limit_error(fast_retry):
    """`post_bytes` shares the same retry/backoff loop as `post_json` — verify
    that sharing didn't drop rate-limit handling for the raw-bytes path.
    """
    slept = fast_retry
    calls = {"n": 0}

    def always_429(req, timeout=None):
        calls["n"] += 1
        raise _http_error(429, "RESOURCE_EXHAUSTED quota")

    with pytest.raises(vertex.RateLimitError):
        _call_bytes(slept, always_429)

    assert calls["n"] == 6  # 1 initial + 5 retries


# --------------------------------------------------------------------------- post_multipart


def _call_multipart(slept, urlopen, fields=None, files=None, headers=None):
    with mock.patch("urllib.request.urlopen", urlopen):
        return retry.post_multipart(
            "http://x",
            fields if fields is not None else {"title": "My Voice", "type": "tts"},
            files if files is not None else [("voices", "sample.mp3", b"fake-audio-bytes", "audio/mpeg")],
            headers if headers is not None else {"Authorization": "Bearer test"},
            on_http_error=lambda c, d: RuntimeError(f"http {c}"),
            on_rate_limited=lambda c, d: vertex.RateLimitError(f"rl {c}"),
            _sleep=slept.append,
            _rand=lambda: 0.0,
        )


def test_post_multipart_success_decodes_json(fast_retry):
    slept = fast_retry
    captured = {}

    def ok(req, timeout=None):
        captured["req"] = req
        return _Resp(b'{"_id": "abc123", "state": "trained"}')

    out = _call_multipart(slept, ok)
    assert out == {"_id": "abc123", "state": "trained"}


def test_post_multipart_sets_content_type_with_boundary(fast_retry):
    slept = fast_retry
    captured = {}

    def ok(req, timeout=None):
        captured["req"] = req
        return _Resp(b'{"_id": "abc123"}')

    _call_multipart(slept, ok)
    ctype = captured["req"].headers.get("Content-type") or captured["req"].headers.get("Content-Type")
    assert ctype is not None
    assert ctype.startswith("multipart/form-data; boundary=")


def test_post_multipart_body_contains_fields_and_file_parts(fast_retry):
    slept = fast_retry
    captured = {}

    def ok(req, timeout=None):
        captured["req"] = req
        return _Resp(b'{"_id": "abc123"}')

    _call_multipart(
        slept,
        ok,
        fields={"title": "My Voice", "train_mode": "fast"},
        files=[
            ("voices", "sample1.mp3", b"AUDIO-ONE", "audio/mpeg"),
            ("voices", "sample2.mp3", b"AUDIO-TWO", "audio/mpeg"),
        ],
    )
    body = captured["req"].data
    assert b'name="title"' in body
    assert b"My Voice" in body
    assert b'name="train_mode"' in body
    assert b"fast" in body
    assert body.count(b'name="voices"') == 2
    assert b"sample1.mp3" in body
    assert b"AUDIO-ONE" in body
    assert b"sample2.mp3" in body
    assert b"AUDIO-TWO" in body
    assert b"Content-Type: audio/mpeg" in body


def _parse_multipart(req) -> list:
    """Parse a captured multipart request back into its parts via the stdlib
    `email` parser, rather than doing substring checks on raw bytes — a real
    structural check that would catch a malformed boundary/missing CRLF/etc.
    """
    import email
    from email.message import Message

    ctype = req.headers.get("Content-type") or req.headers.get("Content-Type")
    msg = email.message_from_bytes(
        f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n".encode() + req.data
    )
    assert isinstance(msg, Message)
    assert msg.is_multipart(), "body did not parse as a well-formed multipart message"
    return msg.get_payload()


def test_post_multipart_body_is_well_formed_multipart(fast_retry):
    # A real parse-back-apart, not substring checks — catches structural
    # defects (bad boundary placement, missing CRLF, unescaped quotes) that
    # `b'name="title"' in body`-style assertions would miss.
    slept = fast_retry
    captured = {}

    def ok(req, timeout=None):
        captured["req"] = req
        return _Resp(b'{"_id": "abc123"}')

    _call_multipart(
        slept,
        ok,
        fields={"title": "My Voice", "train_mode": "fast"},
        files=[
            ("voices", "sample1.mp3", b"AUDIO-ONE", "audio/mpeg"),
            ("voices", "sample2.mp3", b"AUDIO-TWO", "audio/mpeg"),
        ],
    )
    parts = _parse_multipart(captured["req"])
    assert len(parts) == 4  # title, train_mode, voices x2

    def disposition_param(part, key):
        return part.get_param(key, header="Content-Disposition")

    assert disposition_param(parts[0], "name") == "title"
    assert parts[0].get_payload() == "My Voice"
    assert disposition_param(parts[1], "name") == "train_mode"
    assert parts[1].get_payload() == "fast"
    assert disposition_param(parts[2], "name") == "voices"
    assert disposition_param(parts[2], "filename") == "sample1.mp3"
    assert parts[2].get_payload(decode=True) == b"AUDIO-ONE"
    assert disposition_param(parts[3], "filename") == "sample2.mp3"
    assert parts[3].get_payload(decode=True) == b"AUDIO-TWO"


def test_post_multipart_escapes_quotes_in_field_and_filename(fast_retry):
    # A literal `"` in a title/filename must not break the Content-Disposition
    # header — verified by successfully parsing the body back apart, not just
    # checking a substring is present.
    slept = fast_retry
    captured = {}

    def ok(req, timeout=None):
        captured["req"] = req
        return _Resp(b'{"_id": "abc123"}')

    _call_multipart(
        slept,
        ok,
        fields={"title": 'He said "hi"'},
        files=[("voices", 'my "voice".mp3', b"AUDIO", "audio/mpeg")],
    )
    # The body still parses as well-formed multipart (an unescaped `"` in the
    # filename parameter would produce a malformed Content-Disposition header
    # the parser couldn't split cleanly from the next part).
    parts = _parse_multipart(captured["req"])
    assert len(parts) == 2
    # `title`'s own field *name* isn't quoted, so its quoted *value* content
    # ('He said "hi"') is untouched — only header parameter names/filenames
    # (which sit inside a quoted attribute) get percent-encoded.
    assert parts[0].get_param("name", header="Content-Disposition") == "title"
    assert parts[0].get_payload() == 'He said "hi"'
    assert parts[1].get_param("filename", header="Content-Disposition") == 'my %22voice%22.mp3'
    assert parts[1].get_payload(decode=True) == b"AUDIO"


def test_post_multipart_strips_crlf_from_field_value_no_injection(fast_retry):
    # A field value containing CRLF must not be able to inject an extra
    # part/header into the body — this is the header/part-injection case.
    slept = fast_retry
    captured = {}

    def ok(req, timeout=None):
        captured["req"] = req
        return _Resp(b'{"_id": "abc123"}')

    injected = 'He said "hi"\r\nX-Injected: yes\r\n--fake-boundary'
    _call_multipart(slept, ok, fields={"title": injected}, files=[])
    parts = _parse_multipart(captured["req"])
    assert len(parts) == 1  # not split into extra parts by the injected CRLF
    # CR/LF is stripped, so the injected text survives as inert content on one
    # line, rather than becoming a real extra header/part in the multipart body.
    assert parts[0].get_payload() == 'He said "hi"X-Injected: yes--fake-boundary'


def test_post_multipart_retry_backoff_shared_with_post_json(fast_retry):
    """post_multipart shares _post_with_retry's retry/backoff loop — verify
    the sharing didn't drop rate-limit handling for the multipart path.
    """
    slept = fast_retry
    calls = {"n": 0}

    def always_429(req, timeout=None):
        calls["n"] += 1
        raise _http_error(429, "RESOURCE_EXHAUSTED quota")

    with pytest.raises(vertex.RateLimitError):
        _call_multipart(slept, always_429)

    assert calls["n"] == 6  # 1 initial + 5 retries
    assert slept == [20.0, 40.0, 80.0, 160.0, 320.0]
