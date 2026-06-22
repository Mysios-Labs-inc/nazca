"""OpenAI Images backend — gpt-image-2 (text rendering + ad creative).

Direct REST against the public OpenAI API. No SDK, stdlib urllib only — same
shape as the fal / modelark backends.

Auth: `Authorization: Bearer {OPENAI_API_KEY}` header. The key is read lazily
(env > config.ini), so a Vertex-only or fal-only run never reaches for it — BYOK
stays opt-in.

Today this backend covers text-to-image via POST /v1/images/generations. The
gpt-image models always return base64 (no `response_format: url` option), so we
decode `data[0].b64_json` directly. Reference/edit injection lives at
/v1/images/edits (multipart) and is a deliberate follow-up — see image.py.

Pricing note: gpt-image-2 is billed per token (output image tokens dominate and
scale with size × quality), not a flat per-image rate — so there is no constant
cost to declare here. Estimate from the requested size/quality if you need it.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from nazca import config
from nazca.backends.base import Backend
from nazca.backends.vertex import encode_image_b64

OPENAI_BASE = "https://api.openai.com/v1"


class OpenAIError(RuntimeError):
    """Raised when an OpenAI Images dispatch fails (missing key, HTTP error, bad schema)."""


class OpenAIBackend(Backend):
    """OpenAI Images: OPENAI_API_KEY auth + synchronous /images/generations."""

    name = "openai"

    # ------------------------------------------------------------------ auth

    def auth_token(self) -> str:
        """Read OPENAI_API_KEY (env > config file). Only called on real dispatch, never --dry-run."""
        key = config.OPENAI_API_KEY
        if not key:
            raise OpenAIError(
                "OPENAI_API_KEY is not set. Run `nazca login` (or `nazca config set "
                "openai_api_key <key>`) to save it, or export OPENAI_API_KEY for this "
                "session. Get a key from https://platform.openai.com/api-keys."
            )
        return key

    # ------------------------------------------------------------------ URL

    def image_endpoint(self) -> str:
        return f"{OPENAI_BASE}/images/generations"

    def encode_image_b64(self, path, max_edge=None, fmt="PNG"):
        """Shared image (de)coding — reused from the Vertex helper for parity."""
        return encode_image_b64(path, max_edge=max_edge, fmt=fmt)

    # ------------------------------------------------------------------ HTTP

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def post(self, url: str, body: dict, token: str) -> dict:
        """POST a JSON body to the OpenAI Images API, return decoded JSON."""
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers=self._headers(token),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (trusted OpenAI endpoint)
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:600]
            raise OpenAIError(f"HTTP {e.code} from OpenAI: {detail}") from e

    # ------------------------------------------------------------------ image

    def generate_image(self, body: dict) -> bytes:
        """POST /images/generations → decode data[0].b64_json to raw bytes."""
        token = self.auth_token()
        resp = self.post(self.image_endpoint(), body, token)
        data = resp.get("data") or []
        if not data:
            raise OpenAIError(f"no image in OpenAI response: {json.dumps(resp)[:400]}")
        b64 = data[0].get("b64_json")
        if not b64:
            raise OpenAIError(f"no b64_json in OpenAI image entry: {json.dumps(data[0])[:300]}")
        return base64.b64decode(b64)
