"""Tests for nazca.backends.error_hints — actionable hint mapping."""

from __future__ import annotations

import io
import urllib.error
from unittest import mock

import pytest

from nazca.backends import fal, modelark, openai
from nazca.backends.error_hints import hint

# ---------------------------------------------------------------------------
# Unit tests for the hint() helper
# ---------------------------------------------------------------------------


class TestHintHelper:
    def test_openai_401_any_body(self):
        h = hint("openai", 401, "Unauthorized")
        assert "OPENAI_API_KEY" in h
        assert "platform.openai.com/api-keys" in h

    def test_openai_402_billing(self):
        h = hint("openai", 402, "Payment required")
        assert "credit" in h.lower() or "billing" in h.lower()
        assert "promo" in h.lower()

    def test_openai_429_insufficient_quota(self):
        h = hint("openai", 429, '{"error": {"type": "insufficient_quota"}}')
        assert "credit" in h.lower() or "quota" in h.lower()
        assert "promo" in h.lower()

    def test_openai_429_generic_rate_limit(self):
        h = hint("openai", 429, "rate limit exceeded")
        # Should still get a hint (the generic 429 entry)
        assert h != ""
        assert "limit" in h.lower()

    def test_openai_403(self):
        h = hint("openai", 403, "forbidden")
        assert "OPENAI_API_KEY" in h or "permission" in h.lower()

    def test_fal_401(self):
        h = hint("fal", 401, "Unauthorized")
        assert "FAL_KEY" in h
        assert "fal.ai" in h

    def test_fal_403(self):
        h = hint("fal", 403, "forbidden")
        assert "FAL_KEY" in h

    def test_fal_429(self):
        h = hint("fal", 429, "rate limit")
        assert h != ""

    def test_modelark_404_not_found_error(self):
        h = hint("modelark", 404, '{"error": {"code": "InvalidEndpointOrModel.NotFound"}}')
        assert "activated" in h.lower()
        assert "BytePlus" in h or "ark" in h.lower()
        assert "ap-southeast" in h

    def test_modelark_404_generic(self):
        h = hint("modelark", 404, "not found")
        assert "activated" in h.lower() or "model" in h.lower()

    def test_modelark_401(self):
        h = hint("modelark", 401, "Unauthorized")
        assert "ARK_API_KEY" in h

    def test_modelark_429(self):
        h = hint("modelark", 429, "too many requests")
        assert h != ""

    def test_unknown_provider_returns_empty(self):
        h = hint("vertex", 401, "Unauthorized")
        assert h == ""

    def test_unmatched_code_returns_empty(self):
        h = hint("openai", 500, "internal server error")
        assert h == ""

    def test_hint_starts_with_separator(self):
        """All non-empty hints must start with ' — ' so message concatenation looks right."""
        for provider in ("openai", "fal", "modelark"):
            for code in (401, 402, 403, 404, 429):
                h = hint(provider, code, "some error body")
                if h:
                    assert h.startswith(" — "), f"{provider} {code}: hint doesn't start with ' — ': {h!r}"


# ---------------------------------------------------------------------------
# Integration: hints surfaced in backend error types
# ---------------------------------------------------------------------------


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


class TestOpenAIBackendHints:
    def test_401_raises_with_hint(self):
        backend = openai.OpenAIBackend()
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(401, "Unauthorized")):
            with pytest.raises(openai.OpenAIError, match="OPENAI_API_KEY"):
                backend.post("http://x", {}, "tok")

    def test_429_insufficient_quota_raises_with_hint(self):
        backend = openai.OpenAIBackend()
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=_http_error(429, '{"error": {"type": "insufficient_quota"}}'),
        ):
            with pytest.raises(openai.OpenAIError, match="promo"):
                backend.post("http://x", {}, "tok")

    def test_original_detail_preserved(self):
        """The original HTTP body must still appear in the exception message."""
        backend = openai.OpenAIBackend()
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=_http_error(401, "some_unique_detail_xyz"),
        ):
            with pytest.raises(openai.OpenAIError) as exc_info:
                backend.post("http://x", {}, "tok")
        msg = str(exc_info.value)
        assert "some_unique_detail_xyz" in msg
        assert "OPENAI_API_KEY" in msg


class TestFalBackendHints:
    def test_401_raises_with_hint(self):
        backend = fal.FalBackend()
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=_http_error(401, "Unauthorized"),
        ):
            with pytest.raises(fal.FalError, match="FAL_KEY"):
                backend._get("http://x", "tok")


class TestModelArkBackendHints:
    def test_404_not_activated_hint(self):
        backend = modelark.ModelArkBackend()
        body = '{"error": {"code": "InvalidEndpointOrModel.NotFound"}}'
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=_http_error(404, body),
        ):
            with pytest.raises(modelark.ModelArkError, match="activated"):
                backend._get("/some/path")
