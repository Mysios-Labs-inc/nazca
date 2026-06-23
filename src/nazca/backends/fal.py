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

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from nazca import config, retry
from nazca.backends.base import Backend
from nazca.backends.error_hints import hint
from nazca.errors import BackendError
from nazca.errors import RateLimitError as _SharedRateLimitError
from nazca.media import encode_image_b64, encode_image_data_uri, summarize_data_uri

if TYPE_CHECKING:
    from nazca.request import ImageRequest, VideoRequest

# fal expects image_size as a named string ("portrait_16_9" etc.) or {width, height}.
# We map our aspect/size flags to the named-string form.
_FAL_ASPECT_MAP: dict[str, str] = {
    "9:16":  "portrait_16_9",
    "16:9":  "landscape_16_9",
    "1:1":   "square",
    "4:3":   "landscape_4_3",
    "3:4":   "portrait_4_3",
}


class FalError(BackendError):
    """Raised when fal dispatch fails (missing key, HTTP error, timeout, etc.)."""


class FalRateLimitError(FalError, _SharedRateLimitError):
    """429/503/requeue that persisted past NAZCA_MAX_RETRIES retries.

    A distinct type so batch logic can tell "paced wrong" from a real failure.
    Inherits from both ``FalError`` and the shared ``nazca.errors.RateLimitError``
    so callers can catch either.
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
            raise FalError(
                f"HTTP {e.code} from fal: {detail}{hint('fal', e.code, detail)}"
            ) from e

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
            on_http_error=lambda code, detail: FalError(
                f"HTTP {code} from fal: {detail}{hint('fal', code, detail)}"
            ),
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
        """Re-export from media; fal ref inputs are sent as data-URI strings."""
        return encode_image_b64(path, max_edge=max_edge, fmt=fmt)

    def encode_image_data_uri(
        self, path: str | Path, max_edge: int | None = None
    ) -> str:
        """Return a data-URI string for a ref image (fal's expected format for inputs)."""
        return encode_image_data_uri(path, max_edge=max_edge)

    # ------------------------------------------------------------------ image

    def run_image(self, model_id, api, region, req: ImageRequest):
        """fal image generation (FLUX) or a source-image modify op — owns body + plan."""
        if req.op is not None:
            return self._run_modify(model_id, req)

        body = self._fal_image_body(req.prompt, req.refs, req.aspect_ratio)
        url = self.build_url(model_id)

        if req.dry_run:
            plan_body = dict(body)
            if "image_url" in plan_body:
                plan_body["image_url"] = summarize_data_uri(plan_body["image_url"])
            return {
                "url": url,
                "model": model_id,
                "backend": self.name,
                "api": api,
                "refs": len(req.refs),
                "est_cost_usd": req.est_cost_usd,
                "body": plan_body,
            }

        key = self.auth_token()
        return self.submit_and_download(url, body, key, media_type="image")

    def _fal_image_body(self, prompt: str, refs: list[str], aspect_ratio: str | None) -> dict:
        body: dict = {"prompt": prompt}
        if aspect_ratio:
            image_size = _FAL_ASPECT_MAP.get(aspect_ratio)
            if image_size:
                body["image_size"] = image_size
            # unknown aspect → omit (fal will use its default)
        if refs:
            # fal FLUX models accept a single reference image as a data-URI.
            # Multi-ref is unsupported; callers validate, so silently use the first.
            body["image_url"] = self.encode_image_data_uri(refs[0], max_edge=2048)
        return body

    def _run_modify(self, model_id, req: ImageRequest):
        """Source-image modify op (upscale / bg_remove / inpaint / outpaint)."""
        from nazca.image import ImageError

        body: dict = {"image_url": self.encode_image_data_uri(req.source, max_edge=2048)}
        op = req.op
        if op == "upscale":
            body["upscale_factor"] = int(req.upscale_factor)
        elif op == "bg_remove":
            body["output_format"] = "png"
        elif op == "inpaint":
            if not req.mask:
                raise ImageError("inpaint needs a --mask image (white pixels = region to edit)")
            if not req.prompt:
                raise ImageError("inpaint needs a prompt describing the masked region")
            body["mask_url"] = self.encode_image_data_uri(req.mask, max_edge=2048)
            body["prompt"] = req.prompt
        elif op == "outpaint":
            px = int(req.expand)
            body.update({"expand_top": px, "expand_bottom": px, "expand_left": px, "expand_right": px})
        else:
            raise ImageError(f"unknown modify op: {op}")

        url = self.build_url(model_id)
        if req.dry_run:
            # Summarize ONLY the image-bearing fields — never scalars like prompt.
            plan = dict(body)
            for k in ("image_url", "mask_url"):
                if k in plan:
                    plan[k] = summarize_data_uri(plan[k])
            return {"url": url, "model": model_id, "backend": self.name, "op": op, "body": plan}

        key = self.auth_token()
        return self.submit_and_download(url, body, key, media_type="image")

    # ------------------------------------------------------------------ video

    def run_video(self, model_id, region, req: VideoRequest):
        """fal video generation or a video-edit op — owns body + plan."""
        if req.op is not None:
            return self._run_video_edit(model_id, req)

        url = self.build_url(model_id)
        body: dict = {
            "prompt": req.prompt,
            "duration": int(req.duration),
            "aspect_ratio": req.aspect_ratio,
        }
        if req.start:
            body["image_url"] = self.encode_image_data_uri(req.start, max_edge=1280)
        if req.end:
            body["end_image_url"] = self.encode_image_data_uri(req.end, max_edge=1280)

        if req.dry_run:
            plan_body = dict(body)
            for k in ("image_url", "end_image_url"):
                if k in plan_body:
                    plan_body[k] = summarize_data_uri(plan_body[k])
            return {"url": url, "model": model_id, "backend": self.name, **plan_body}

        key = self.auth_token()
        return self.submit_and_download(url, body, key, media_type="video")

    def _run_video_edit(self, model_id, req: VideoRequest):
        """Video-edit op (reframe / v2v / extend) — source is a public video URL."""
        from nazca.video import VeoError

        src = str(req.source)
        url = self.build_url(model_id)
        op = req.op
        if op == "reframe":
            body: dict = {"video_url": src, "aspect_ratio": req.aspect_ratio}
            if req.prompt:
                body["prompt"] = req.prompt  # optional: guides inpainting of exposed regions
        elif op == "v2v":
            if not req.prompt:
                raise VeoError("v2v needs a prompt (the edit instruction)")
            body = {"video_url": src, "prompt": req.prompt}
        elif op == "extend":
            if not req.prompt:
                raise VeoError("extend needs a prompt")
            if int(req.duration) not in (5, 8):
                raise VeoError("extend --duration must be 5 or 8 (seconds added to the clip)")
            body = {"video_url": src, "prompt": req.prompt, "duration": str(int(req.duration))}
        else:
            raise VeoError(f"video-edit op '{op}' is not wired yet")

        if req.dry_run:
            return {"url": url, "model": model_id, "backend": self.name, "op": op, **body}

        key = self.auth_token()
        return self.submit_and_download(url, body, key, media_type="video")
