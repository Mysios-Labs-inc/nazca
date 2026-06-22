"""fal.ai backend — queue-based REST, no fal SDK.

Lifecycle (differs from Vertex's inline-response pattern):
  1. POST  https://queue.fal.run/{fal_model_id}   → {request_id, status_url, response_url}
  2. GET   status_url   (poll until status == "COMPLETED")
  3. GET   response_url → result JSON with media URLs (images[0].url or video.url)
  4. GET   media URL    → raw bytes

Auth: `Authorization: Key {FAL_KEY}` header.  Key is read lazily from env —
a Vertex-only run never touches it.  BYOK: keep FAL_KEY in your shell profile /
secrets manager, never in code or CLI flags.
"""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from nazca import config, retry
from nazca.backends.base import Backend


class FalError(RuntimeError):
    """Raised when fal dispatch fails (missing key, HTTP error, timeout, etc.)."""


class FalRateLimitError(FalError):
    """429/503/requeue that persisted past NAZCA_MAX_RETRIES retries.

    A distinct type so batch logic can tell "paced wrong" from a real failure.
    """


class FalBackend(Backend):
    """fal.ai: FAL_KEY env auth + queue REST (submit → poll → download)."""

    name = "fal"

    # ------------------------------------------------------------------ auth

    def auth_token(self) -> str:
        """Read FAL_KEY (env > config file).  Only called on real dispatch, never --dry-run."""
        key = config.FAL_KEY
        if not key:
            raise FalError(
                "FAL_KEY is not set. Run `nazca login` (or `nazca config set "
                "fal_key <key>`) to save it, or export FAL_KEY for this session. "
                "Get a key from the fal.ai dashboard → API keys."
            )
        return key

    # ------------------------------------------------------------------ URL

    def build_url(self, model: str, op: str | None = None, location: str | None = None) -> str:
        """fal queue endpoint — op and location are unused (fal has no per-op paths)."""
        return f"https://queue.fal.run/{model}"

    # ------------------------------------------------------------------ HTTP helpers

    def _get(self, url: str, key: str) -> dict:
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Key {key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted fal endpoint)
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:600]
            raise FalError(f"HTTP {e.code} from fal: {detail}") from e

    def post(self, url: str, body: dict, token: str) -> dict:
        """POST JSON to fal queue endpoint with bounded backoff (item 1A), return JSON.

        Retries on 429/503 and on fal's `x-fal-needs-retry` server-side requeue signal.
        """
        return retry.post_json(
            url,
            body,
            headers={
                "Authorization": f"Key {token}",
                "Content-Type": "application/json",
            },
            on_http_error=lambda code, detail: FalError(f"HTTP {code} from fal: {detail}"),
            on_rate_limited=lambda code, detail: FalRateLimitError(
                f"fal rate limit (HTTP {code}) persisted after retries: {detail}"
            ),
        )

    # ------------------------------------------------------------------ queue lifecycle

    def submit_and_download(self, url: str, body: dict, token: str, media_type: str = "image") -> bytes:
        """Submit to fal queue, poll until done, download and return raw bytes.

        media_type: "image" → result["images"][0]["url"], or singular
                              result["image"]["url"] (modify models: upscaler, birefnet)
                    "video" → result["video"]["url"]
        """
        # 1. Submit
        submit = self.post(url, body, token)
        status_url = submit.get("status_url")
        response_url = submit.get("response_url")
        if not status_url or not response_url:
            raise FalError(f"fal submit missing status/response URLs: {json.dumps(submit)[:400]}")

        # 2. Poll
        for _ in range(config.POLL_MAX_TRIES):
            time.sleep(config.POLL_INTERVAL)
            status = self._get(status_url, token)
            if status.get("status") == "COMPLETED":
                break
            if status.get("status") in ("FAILED", "CANCELLED"):
                raise FalError(f"fal job {status.get('status')}: {json.dumps(status)[:400]}")
        else:
            raise FalError(
                f"timed out waiting for fal job after "
                f"{config.POLL_MAX_TRIES * config.POLL_INTERVAL}s"
            )

        # 3. Fetch result JSON
        result = self._get(response_url, token)

        # 4. Extract media URL
        if media_type == "video":
            video = result.get("video") or {}
            media_url = video.get("url")
            if not media_url:
                raise FalError(f"no video URL in fal result: {json.dumps(result)[:400]}")
        else:
            # Most fal image models return images[0].url; the modify models
            # (clarity-upscaler, birefnet) return a singular image.url. Accept both.
            images = result.get("images") or []
            single = result.get("image") if isinstance(result.get("image"), dict) else None
            media_url = (images[0].get("url") if images else None) or (single.get("url") if single else None)
            if not media_url:
                raise FalError(f"no image URL in fal result: {json.dumps(result)[:400]}")

        # 5. Download bytes
        dl_req = urllib.request.Request(media_url, method="GET")
        try:
            with urllib.request.urlopen(dl_req) as resp:  # noqa: S310 (fal CDN)
                return resp.read()
        except urllib.error.HTTPError as e:
            raise FalError(f"HTTP {e.code} downloading fal media: {e.reason}") from e

    # ------------------------------------------------------------------ image encoding

    def encode_image_b64(
        self, path: str | Path, max_edge: int | None = None, fmt: str = "JPEG"
    ) -> tuple[str, str]:
        """Return (base64, mime) for an image, optionally downscaled to max_edge.

        Identical to Vertex's implementation — fal ref inputs are sent as
        data-URI strings built from this output.
        """
        img = Image.open(path)
        img = img.convert("RGB") if fmt == "JPEG" else img.convert("RGBA")
        if max_edge:
            w, h = img.size
            scale = min(1.0, max_edge / max(w, h))
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        mime = "image/jpeg" if fmt == "JPEG" else "image/png"
        return base64.b64encode(buf.getvalue()).decode(), mime

    def encode_image_data_uri(
        self, path: str | Path, max_edge: int | None = None
    ) -> str:
        """Return a data-URI string for a ref image (fal's expected format for inputs)."""
        b64, mime = self.encode_image_b64(path, max_edge=max_edge, fmt="PNG")
        return f"data:{mime};base64,{b64}"
