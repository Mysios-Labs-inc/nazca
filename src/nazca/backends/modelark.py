"""ByteDance ModelArk direct backend (Seedream image + Seedance video).

WARNING: ModelArk API IDs, endpoints, and schema are UNVERIFIED (dry-run only).
Benchmark against fal before real spend — the ~25% cheaper claim is unverified.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from nazca import config, retry
from nazca.backends.base import Backend
from nazca.backends.error_hints import hint
from nazca.errors import BackendError
from nazca.errors import RateLimitError as _SharedRateLimitError
from nazca.media import encode_image_b64, encode_image_data_uri, summarize_data_uri

if TYPE_CHECKING:
    from nazca.request import ImageRequest, VideoRequest

ARK_BASE = "https://ark.ap-southeast.bytepluses.com/api/v3"  # verify against ModelArk docs

# --- Seedream sizing (verified against BytePlus ModelArk image API, 2026-06-22) ---
_SEEDREAM_MAX_REFS = 14  # API ceiling for seedream-4-0 multi-reference input
# Named-resolution square edge (px); the doc's 1:1 dimensions per resolution.
_SEEDREAM_EDGE: dict[str, int] = {"1K": 1024, "2K": 2048, "4K": 4096}
# Documented valid total-pixel range for seedream-4-0 Method 2 (w*h).
_SEEDREAM_MIN_PX = 1280 * 720  # 921,600
_SEEDREAM_MAX_PX = 4096 * 4096  # 16,777,216


def _seedream_size(size: str | None, aspect_ratio: str | None) -> str | None:
    """Map nazca --size (+ --aspect) to ModelArk's `size` field.

    Seedream has no aspect field — aspect is expressed by giving explicit pixel
    dimensions. With a named --size and an explicit W:H aspect we compute a
    "<w>x<h>" that holds the aspect at roughly the resolution's pixel budget
    (rounded to /16, clamped to the valid range). Without a usable aspect we pass
    the named resolution and let the model pick dimensions (e.g. follow the ref).
    """
    if not size:
        return None
    edge = _SEEDREAM_EDGE.get(size.upper()) if isinstance(size, str) else None
    if edge is None or not aspect_ratio or ":" not in aspect_ratio:
        return size  # named resolution, or a caller-supplied raw "WxH"
    try:
        aw, ah = (float(x) for x in aspect_ratio.split(":", 1))
        if aw <= 0 or ah <= 0:
            return size
    except ValueError:
        return size
    budget = edge * edge
    h = (budget * ah / aw) ** 0.5
    w = h * aw / ah
    w = max(16, round(w / 16) * 16)
    h = max(16, round(h / 16) * 16)
    if not (_SEEDREAM_MIN_PX <= w * h <= _SEEDREAM_MAX_PX):
        return size  # fall back to the named resolution rather than an invalid dim
    return f"{w}x{h}"


def _seedream_body(prompt: str, refs: list[str], aspect_ratio: str | None, size: str | None, backend) -> dict:
    """Build the ModelArk Seedream request body (sends refs as the `image` field)."""
    body: dict = {
        "model": None,  # filled by caller (resolved model id)
        "prompt": prompt,
        "sequential_image_generation": "disabled",  # one image out (not a batch)
        "response_format": "url",
        "watermark": False,  # no "AI generated" stamp on brand assets
    }
    sd_size = _seedream_size(size, aspect_ratio)
    if sd_size:
        body["size"] = sd_size
    if refs:
        encoded = [backend.encode_image_data_uri(r, max_edge=2048) for r in refs[:_SEEDREAM_MAX_REFS]]
        # one ref → string; many → array (Seedream accepts both)
        body["image"] = encoded[0] if len(encoded) == 1 else encoded
    return body


class ModelArkError(BackendError):
    """Raised when a ModelArk request fails (missing key, HTTP error, timeout, etc.)."""


class ModelArkRateLimitError(ModelArkError, _SharedRateLimitError):
    """429/503/RESOURCE_EXHAUSTED that persisted past NAZCA_MAX_RETRIES retries.

    A distinct type so batch logic can tell "paced wrong" from a real failure.
    Inherits from both ``ModelArkError`` and the shared ``nazca.errors.RateLimitError``
    so callers can catch either.
    """


class ModelArkBackend(Backend):
    name = "modelark"

    def image_endpoint(self) -> str:
        return f"{ARK_BASE}/images/generations"  # verify against ModelArk docs

    def video_endpoint(self) -> str:
        return f"{ARK_BASE}/contents/generations/tasks"  # verify against ModelArk docs

    def encode_image_b64(self, path, max_edge=None, fmt="PNG"):
        """Re-export from media for backend interface."""
        return encode_image_b64(path, max_edge=max_edge, fmt=fmt)

    def encode_image_data_uri(self, path, max_edge: int | None = None) -> str:
        """ModelArk takes image inputs as data URIs (verify against docs)."""
        return encode_image_data_uri(path, max_edge=max_edge)

    def auth_token(self) -> str:
        """Read ARK_API_KEY (env > config file) lazily — never called during dry-run."""
        key = config.ARK_API_KEY
        if not key:
            raise ModelArkError(
                "ARK_API_KEY is not set. Run `nazca login` (or `nazca config set "
                "ark_api_key <key>`) to save it, or export ARK_API_KEY for this session. "
                "See https://ark.bytepluses.com for credentials."
            )
        return key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.auth_token()}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> dict:
        """POST to ModelArk with bounded backoff on 429/503/RESOURCE_EXHAUSTED (item 1A)."""
        url = f"{ARK_BASE}{path}"
        return retry.post_json(
            url,
            body,
            headers=self._headers(),
            timeout=30,
            on_http_error=lambda code, detail: ModelArkError(
                f"ModelArk HTTP {code}: {detail}{hint('modelark', code, detail)}"
            ),
            on_rate_limited=lambda code, detail: ModelArkRateLimitError(
                f"ModelArk rate limit (HTTP {code}) persisted after retries: {detail}"
            ),
        )

    def _get(self, path: str) -> dict:
        url = f"{ARK_BASE}{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise ModelArkError(
                f"ModelArk HTTP {exc.code}: {detail}{hint('modelark', exc.code, detail)}"
            ) from exc

    # ------------------------------------------------------------------ run seam

    def run_image(self, resolved, req: ImageRequest):
        """Seedream image generation — native multi-reference image-to-image.

        Refs go in the `image` field; the body is built once and reused for dry-run
        and real send (only base64 blobs are summarized) so the planned JSON matches
        what's POSTed. Requires model activation in the BytePlus console + balance.
        """
        model_id, api = resolved.provider_id, resolved.api
        body = _seedream_body(req.prompt, req.refs, req.aspect_ratio, req.size, self)
        body["model"] = model_id

        if req.dry_run:
            plan_body = dict(body)
            if "image" in plan_body:
                plan_body["image"] = summarize_data_uri(plan_body["image"])
            return {
                "url": self.image_endpoint(),
                "model": model_id,
                "backend": self.name,
                "api": api,
                "refs": len(req.refs),
                "est_cost_usd": req.est_cost_usd,
                "body": plan_body,
            }

        return self.generate_image(model_id, body)

    def run_video(self, resolved, req: VideoRequest):
        """Seedance async video. Schema UNVERIFIED — dry-run safe; benchmark before spend.

        The dry-run plan omits a top-level `model` key (the model id rides inside the
        body's `content`/`model`), matching the original ModelArk video plan shape.
        """
        model_id = resolved.provider_id
        content: list[dict] = [{"type": "text", "text": req.prompt}]  # verify schema
        if req.start:
            start_uri = self.encode_image_data_uri(req.start, max_edge=1280)
            content.append({"type": "image_url", "image_url": {"url": start_uri}})  # verify schema
        body: dict = {
            "model": model_id,
            "content": content,
            "duration": int(req.duration),
            "aspect_ratio": req.aspect_ratio,
        }
        if req.end:
            body["end_image_url"] = self.encode_image_data_uri(req.end, max_edge=1280)  # verify field

        if req.dry_run:
            preview = json.loads(json.dumps(body))
            for part in preview["content"]:
                u = part.get("image_url", {}).get("url", "")
                if isinstance(u, str) and u.startswith("data:"):
                    part["image_url"]["url"] = f"<data-uri {len(u.split(',', 1)[-1])} b64>"
            if isinstance(preview.get("end_image_url"), str) and preview["end_image_url"].startswith("data:"):
                preview["end_image_url"] = f"<data-uri {len(preview['end_image_url'].split(',', 1)[-1])} b64>"
            return {"url": self.video_endpoint(), "backend": self.name, **preview}

        return self.generate_video(model_id, body)

    # ------------------------------------------------------------------
    # Image — synchronous (Seedream)  # verify against ModelArk docs
    # ------------------------------------------------------------------
    def generate_image(self, model_id: str, body: dict) -> bytes:
        """POST /images/generations → download URL or decode b64."""
        resp = self._post("/images/generations", body)  # verify schema
        data = resp.get("data", [{}])[0]
        if "b64_json" in data:
            import base64

            return base64.b64decode(data["b64_json"])
        url = data.get("url")
        if not url:
            raise ModelArkError(f"No image URL in response: {resp}")
        with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310
            return r.read()

    # ------------------------------------------------------------------
    # Video — async task (Seedance)  # verify against ModelArk docs
    # ------------------------------------------------------------------
    def generate_video(self, model_id: str, body: dict) -> bytes:
        """POST /contents/generations/tasks → poll → download."""
        resp = self._post("/contents/generations/tasks", body)  # verify schema
        task_id = resp.get("id")
        if not task_id:
            raise ModelArkError(f"No task id in response: {resp}")

        for _ in range(config.POLL_MAX_TRIES):
            time.sleep(config.POLL_INTERVAL)
            status_resp = self._get(f"/contents/generations/tasks/{task_id}")  # verify path
            status = status_resp.get("status")
            if status == "succeeded":
                video_url = status_resp.get("content", {}).get("video_url")  # verify field
                if not video_url:
                    raise ModelArkError(f"No video_url in succeeded task: {status_resp}")
                with urllib.request.urlopen(video_url, timeout=120) as r:  # noqa: S310
                    return r.read()
            if status in ("failed", "cancelled"):
                raise ModelArkError(f"Task {task_id} {status}: {status_resp}")
        raise ModelArkError(f"Seedance task {task_id} timed out after {config.POLL_MAX_TRIES} polls")
