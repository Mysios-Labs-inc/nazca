"""OpenAI Images backend — gpt-image-2 (text rendering + ad creative).

Direct REST against the public OpenAI API. No SDK, stdlib urllib only — same
shape as the fal / modelark backends.

Auth: `Authorization: Bearer {OPENAI_API_KEY}` header. The key is read lazily
(env > config.ini), so a Vertex-only or fal-only run never reaches for it — BYOK
stays opt-in.

Two operations, both synchronous and both returning base64 (gpt-image models
have no `response_format: url` option, so we decode `data[0].b64_json` directly):

  - text-to-image   POST /v1/images/generations   (JSON body)
  - reference/edit  POST /v1/images/edits          (multipart/form-data)

The edits path is what powers ad workflows — drop a product shot or logo in as a
reference and let gpt-image-2 compose around it. gpt-image-2 accepts up to 5
input images (sent as repeated `image[]` parts).

Pricing note: gpt-image-2 is billed per token (output image tokens dominate and
scale with size × quality), not a flat per-image rate — so there is no constant
cost to declare here. Estimate from the requested size/quality if you need it.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
import uuid

from nazca import config
from nazca.backends.base import Backend
from nazca.backends.error_hints import hint
from nazca.media import encode_image_b64, encode_image_bytes

OPENAI_BASE = "https://api.openai.com/v1"
MAX_EDIT_IMAGES = 5  # gpt-image-2 input-image cap (OpenAI Images API)


class OpenAIError(RuntimeError):
    """Raised when an OpenAI Images dispatch fails (missing key, HTTP error, bad schema)."""


class OpenAIBackend(Backend):
    """OpenAI Images: OPENAI_API_KEY auth + synchronous /images/generations."""

    name = "openai"

    # Token usage from the most recent real dispatch (set by generate_image /
    # edit_image). Lets the CLI report the ACTUAL cost after a gpt-image-2 run.
    # None until a real call lands (dry-runs never touch the backend).
    last_usage: dict | None = None

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

    def edit_endpoint(self) -> str:
        return f"{OPENAI_BASE}/images/edits"

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
            raise OpenAIError(
                f"HTTP {e.code} from OpenAI: {detail}{hint('openai', e.code, detail)}"
            ) from e

    # ------------------------------------------------------------------ image

    def generate_image(self, body: dict) -> bytes:
        """POST /images/generations → decode data[0].b64_json to raw bytes."""
        token = self.auth_token()
        resp = self.post(self.image_endpoint(), body, token)
        self.last_usage = resp.get("usage")  # token counts → actual cost (gpt-image-2)
        return self._extract_b64(resp)

    # ------------------------------------------------------------------ edit (multipart)

    def edit_image(self, fields: dict, image_paths: list[str], max_edge: int | None = 2048) -> bytes:
        """POST /images/edits (multipart) with reference image(s) → raw bytes.

        `fields` are the text form fields (model, prompt, size, quality, n).
        Each path in `image_paths` becomes a repeated `image[]` part; PNGs are
        re-encoded (and optionally downscaled to `max_edge`) to keep payloads sane.
        """
        if not image_paths:
            raise OpenAIError("edit_image requires at least one reference image")
        if len(image_paths) > MAX_EDIT_IMAGES:
            raise OpenAIError(
                f"gpt-image-2 accepts at most {MAX_EDIT_IMAGES} reference images, got {len(image_paths)}"
            )

        token = self.auth_token()
        images = [(encode_image_bytes(p, max_edge), f"ref{i}.png") for i, p in enumerate(image_paths)]
        body, content_type = self._multipart(fields, images)

        req = urllib.request.Request(
            self.edit_endpoint(),
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310 (trusted OpenAI endpoint)
                decoded = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:600]
            raise OpenAIError(
                f"HTTP {e.code} from OpenAI: {detail}{hint('openai', e.code, detail)}"
            ) from e
        self.last_usage = decoded.get("usage")  # token counts → actual cost (gpt-image-2)
        return self._extract_b64(decoded)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _extract_b64(resp: dict) -> bytes:
        data = resp.get("data") or []
        if not data:
            raise OpenAIError(f"no image in OpenAI response: {json.dumps(resp)[:400]}")
        b64 = data[0].get("b64_json")
        if not b64:
            raise OpenAIError(f"no b64_json in OpenAI image entry: {json.dumps(data[0])[:300]}")
        return base64.b64decode(b64)

    @staticmethod
    def _multipart(fields: dict, images: list[tuple[bytes, str]]) -> tuple[bytes, str]:
        """Build a multipart/form-data body. images → repeated `image[]` parts."""
        boundary = f"----nazca{uuid.uuid4().hex}"
        crlf = b"\r\n"
        out = bytearray()
        for name, value in fields.items():
            out += f"--{boundary}".encode() + crlf
            out += f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf
            out += str(value).encode() + crlf
        for data, filename in images:
            out += f"--{boundary}".encode() + crlf
            out += (
                f'Content-Disposition: form-data; name="image[]"; filename="{filename}"'.encode()
                + crlf
            )
            out += b"Content-Type: image/png" + crlf + crlf
            out += data + crlf
        out += f"--{boundary}--".encode() + crlf
        return bytes(out), f"multipart/form-data; boundary={boundary}"
