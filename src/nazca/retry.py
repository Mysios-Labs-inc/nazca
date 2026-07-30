"""Bounded exponential backoff for rate-limited provider POSTs (Tier 1, item A).

Vertex image gen is capped at 2 req/min per base model and `post()` historically
raised immediately on HTTP 429 — every caller (CLI, MCP, batch) inherited zero
resilience, so the retry had to be bolted on in external bash scripts. This module
ports that resilience into the backends so it lives in one place.

Retryable signals: HTTP 429, HTTP 503, a `RESOURCE_EXHAUSTED` status in the body,
or fal's `x-fal-needs-retry` requeue header. Delays grow geometrically from
`NAZCA_BACKOFF_BASE` seconds (default 20 → 20, 40, 80, 160, 320…), each jittered up
to +25% to avoid synchronized retry storms across lanes. When the server sends a
`Retry-After` header (seconds), it is used as a *floor* for that attempt's delay —
so a provider that tells us exactly when the quota refills gets honored instead of
us guessing short and burning a retry.

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
import uuid
from typing import Callable

from nazca.log import get_logger

logger = get_logger("retry")

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


def _needs_retry_header(headers: dict | None) -> bool:
    """fal documents a server-side requeue via this header, sent on a 2xx response.

    The header only appears on fal responses, so the check is a no-op elsewhere.
    """
    return bool(headers) and str(headers.get("x-fal-needs-retry", "")).lower() == "true"


def _is_retryable(code: int, body: str, headers: dict | None) -> bool:
    if code in RETRYABLE_STATUS:
        return True
    if "RESOURCE_EXHAUSTED" in body:
        return True
    return _needs_retry_header(headers)


def _retry_after_seconds(headers: dict | None) -> float:
    """Seconds from a `Retry-After` header, or 0.0 if absent/unparseable.

    Only the integer-seconds form is honored (the form Vertex/Google send). The
    HTTP-date form is rare here and treated as absent — we fall back to the
    computed exponential delay, which is never worse than guessing.
    """
    if not headers:
        return 0.0
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return 0.0
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return 0.0


def _post_with_retry(
    url: str,
    data: bytes,
    headers: dict,
    *,
    on_http_error: Callable[[int, str], Exception],
    on_rate_limited: Callable[[int, str], Exception],
    decode: Callable[[bytes, dict], object],
    timeout: float | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _rand: Callable[[], float] = random.random,
) -> object:
    """Shared retry/backoff loop for a POST whose success body `decode` turns into
    the return value — `json.loads` for `post_json`/`post_multipart`, raw
    passthrough (with a Content-Type check) for `post_bytes`. See `post_json`
    for the retry/backoff semantics; this is the body all three public
    helpers share so the retry logic lives in exactly one place.

    `data` is the already-encoded request body (JSON bytes for `post_json`/
    `post_bytes`, a hand-built multipart/form-data body for `post_multipart`) —
    encoding happens once in the caller, before the retry loop, so each retry
    attempt reuses the identical bytes rather than re-encoding.

    `decode` also receives the response headers (dict) alongside the payload,
    so `post_bytes` can inspect Content-Type before treating a 2xx body as
    valid — see its docstring for why.
    """
    retries = max_retries()
    base = backoff_base()

    def _backoff(attempt: int, floor: float = 0.0) -> None:
        delay = base * (2 ** attempt)
        delay += delay * 0.25 * _rand()  # jitter: up to +25%
        _sleep(max(delay, floor))  # honor a server Retry-After as a lower bound

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted provider endpoint)
                payload = resp.read()
                resp_headers = dict(resp.headers or {})
                # fal requeue signal rides on a successful (2xx) response.
                if _needs_retry_header(resp_headers):
                    if attempt < retries:
                        base_delay = base * (2 ** attempt)
                        logger.warning(
                            f"Retry attempt {attempt + 1}/{retries + 1}: "
                            f"x-fal-needs-retry. Backoff: {base_delay:.1f}s (+jitter)"
                        )
                        _backoff(attempt)
                        continue
                    raise on_rate_limited(
                        getattr(resp, "status", 0), "server requested retry (x-fal-needs-retry)"
                    )
                return decode(payload, resp_headers)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:600]
            err_headers = dict(e.headers or {})
            retryable = _is_retryable(e.code, detail, err_headers)
            if retryable and attempt < retries:
                reason_parts = [f"HTTP {e.code}"]
                if "RESOURCE_EXHAUSTED" in detail:
                    reason_parts.append("RESOURCE_EXHAUSTED")
                reason = ", ".join(reason_parts)
                retry_after = _retry_after_seconds(err_headers)
                base_delay = max(base * (2 ** attempt), retry_after)
                logger.warning(
                    f"Retry attempt {attempt + 1}/{retries + 1}: "
                    f"{reason}. Backoff: {base_delay:.1f}s (+jitter)"
                )
                _backoff(attempt, retry_after)
                continue
            if retryable:
                raise on_rate_limited(e.code, detail)
            raise on_http_error(e.code, detail)
    raise AssertionError("unreachable")  # loop always returns or raises


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

    Retries up to `NAZCA_MAX_RETRIES` times on 429/503/RESOURCE_EXHAUSTED, and on
    fal's `x-fal-needs-retry` requeue header — which fal sends on a 2xx response, so
    it is checked on the success path, not just on HTTP errors. When retries are
    exhausted on a rate-limit signal, raises the exception built by
    `on_rate_limited(code, detail)`; for any other HTTP error, raises
    `on_http_error(code, detail)`. Both callbacks receive the status code and the
    (truncated) response body so callers keep their own error types and messages.

    `_sleep`/`_rand` are injectable for testing.
    """
    return _post_with_retry(
        url,
        json.dumps(body).encode(),
        headers,
        on_http_error=on_http_error,
        on_rate_limited=on_rate_limited,
        decode=lambda payload, resp_headers: json.loads(payload.decode()),
        timeout=timeout,
        _sleep=_sleep,
        _rand=_rand,
    )


# Content-Types that mean "this 2xx body isn't the media it claims to be" — an
# expired signed URL or partial-failure response can return an error body (JSON/
# XML/HTML) at HTTP 200, which would otherwise be written straight to the output
# file as if it were real audio. A narrow negative check (reject known non-media
# types) rather than a positive whitelist, since real audio Content-Types vary
# per provider/format and aren't worth enumerating — same approach as Atlas's
# `_poll` Content-Type guard (issue #122 phase A4).
_NON_MEDIA_CONTENT_TYPES = frozenset(
    {"application/json", "application/xml", "text/xml", "text/html"}
)


def post_bytes(
    url: str,
    body: dict,
    headers: dict,
    *,
    on_http_error: Callable[[int, str], Exception],
    on_rate_limited: Callable[[int, str], Exception],
    timeout: float | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _rand: Callable[[], float] = random.random,
) -> bytes:
    """POST a JSON body with the same bounded exponential backoff as `post_json`,
    but return the raw response body as `bytes` instead of JSON-decoding it.

    For providers (e.g. Fish Audio's `/v1/tts`, ElevenLabs' `/v1/text-to-speech`
    and `/v1/sound-generation`) whose success response is a raw audio stream, not
    a JSON envelope. Retry/backoff semantics are identical to `post_json` — see
    its docstring. A 2xx response whose Content-Type is JSON/XML/HTML (see
    `_NON_MEDIA_CONTENT_TYPES`) raises `on_http_error(200, ...)` instead of being
    returned as if it were audio.
    """

    def _decode(payload: bytes, resp_headers: dict) -> bytes:
        ctype = (resp_headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype in _NON_MEDIA_CONTENT_TYPES:
            raise on_http_error(
                200, f"response Content-Type is {ctype!r}, not audio: {payload[:400]!r}"
            )
        return payload

    return _post_with_retry(
        url,
        json.dumps(body).encode(),
        headers,
        on_http_error=on_http_error,
        on_rate_limited=on_rate_limited,
        decode=_decode,
        timeout=timeout,
        _sleep=_sleep,
        _rand=_rand,
    )


def _strip_crlf(value: str) -> str:
    """Drop CR/LF from a multipart header value — a value containing a newline
    would otherwise let its caller inject an extra part/header into the body.
    """
    return value.replace("\r", "").replace("\n", "")


def _escape_header_value(value: str) -> str:
    """Escape a value placed inside a quoted `Content-Disposition` parameter
    (`name="..."`/`filename="..."`): strip CR/LF (header/part injection) and
    percent-encode `"` (a literal quote would otherwise terminate the
    parameter early and produce a malformed header).
    """
    return _strip_crlf(value).replace('"', "%22")


def _build_multipart_body(
    fields: dict[str, str], files: list[tuple[str, str, bytes, str]]
) -> tuple[bytes, str]:
    """Hand-build a `multipart/form-data` body; return `(data, content_type)`.

    The stdlib has no multipart encoder and this project takes no `requests`/
    `httpx` dependency (see pyproject.toml — only `click` + `Pillow` are core
    deps), so the body is built by hand: a random boundary (`uuid.uuid4().hex`),
    one part per `fields` entry (simple `name` form fields) followed by one part
    per `files` entry (`field_name, filename, content_bytes, content_type` —
    `field_name` may repeat across entries, e.g. several `"voices"` files for one
    array field), each terminated with `\\r\\n`, and a final `--boundary--\\r\\n`
    sentinel.

    Every `name`/`filename`/field value is escaped before being placed in a
    header line: CR/LF are stripped (otherwise a value containing a newline
    could inject an extra part into the body — header/part injection) and `"`
    is percent-encoded (otherwise a quote in e.g. a filename produces a
    malformed `Content-Disposition` header). Field *content* (`content_bytes`)
    is untouched — only the surrounding header text is escaped.

    Shared by `post_multipart` (JSON response) and `post_multipart_bytes` (raw
    audio response) so the encoding lives in exactly one place.
    """
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{_escape_header_value(name)}"\r\n\r\n'
            f'{_strip_crlf(value)}\r\n'.encode()
        )
    for field_name, filename, content_bytes, content_type in files:
        part_header = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{_escape_header_value(field_name)}"; '
            f'filename="{_escape_header_value(filename)}"\r\n'
            f'Content-Type: {_strip_crlf(content_type)}\r\n\r\n'
        ).encode()
        parts.append(part_header + content_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    data = b"".join(parts)
    return data, f"multipart/form-data; boundary={boundary}"


def post_multipart(
    url: str,
    fields: dict[str, str],
    files: list[tuple[str, str, bytes, str]],
    headers: dict,
    *,
    on_http_error: Callable[[int, str], Exception],
    on_rate_limited: Callable[[int, str], Exception],
    timeout: float | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _rand: Callable[[], float] = random.random,
) -> dict:
    """POST a hand-built `multipart/form-data` body, return decoded JSON.

    See `_build_multipart_body` for the encoding. Unlike `post_json`/
    `post_bytes` (where the caller sets its own `Content-Type`), if the
    caller sets `Content-Type` in `headers` here it is silently overwritten —
    this function always sets it to `multipart/form-data; boundary=<boundary>`
    itself, since the boundary is generated inside this call.

    Retry/backoff semantics are identical to `post_json` — see its docstring.
    `_sleep`/`_rand` are injectable for testing.
    """
    data, content_type = _build_multipart_body(fields, files)
    post_headers = dict(headers)
    post_headers["Content-Type"] = content_type

    return _post_with_retry(
        url,
        data,
        post_headers,
        on_http_error=on_http_error,
        on_rate_limited=on_rate_limited,
        decode=lambda payload, resp_headers: json.loads(payload.decode()),
        timeout=timeout,
        _sleep=_sleep,
        _rand=_rand,
    )


def post_multipart_bytes(
    url: str,
    fields: dict[str, str],
    files: list[tuple[str, str, bytes, str]],
    headers: dict,
    *,
    on_http_error: Callable[[int, str], Exception],
    on_rate_limited: Callable[[int, str], Exception],
    timeout: float | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _rand: Callable[[], float] = random.random,
) -> bytes:
    """POST a hand-built `multipart/form-data` body, return the raw response
    body as `bytes` instead of JSON-decoding it.

    Combines `post_multipart`'s request encoding (see `_build_multipart_body`)
    with `post_bytes`'s response handling: for providers (e.g. ElevenLabs'
    `POST /v1/speech-to-speech/{voice_id}`, issue #122 phase A3) whose request
    is a multipart file upload but whose *success* response is a raw audio
    stream, not a JSON envelope — neither existing helper covers that
    combination on its own. Same `_NON_MEDIA_CONTENT_TYPES` guard as
    `post_bytes`: a 2xx response whose Content-Type is JSON/XML/HTML raises
    `on_http_error(200, ...)` instead of being returned as if it were audio.

    Retry/backoff semantics are identical to `post_json` — see its docstring.
    `_sleep`/`_rand` are injectable for testing.
    """
    data, content_type = _build_multipart_body(fields, files)
    post_headers = dict(headers)
    post_headers["Content-Type"] = content_type

    def _decode(payload: bytes, resp_headers: dict) -> bytes:
        ctype = (resp_headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype in _NON_MEDIA_CONTENT_TYPES:
            raise on_http_error(
                200, f"response Content-Type is {ctype!r}, not audio: {payload[:400]!r}"
            )
        return payload

    return _post_with_retry(
        url,
        data,
        post_headers,
        on_http_error=on_http_error,
        on_rate_limited=on_rate_limited,
        decode=_decode,
        timeout=timeout,
        _sleep=_sleep,
        _rand=_rand,
    )
