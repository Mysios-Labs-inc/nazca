"""Atlas Cloud backend — unified async media API (image + video).

Atlas Cloud aggregates 300+ media models behind ONE async REST API. Unlike the
LLM endpoint (which is OpenAI-compatible at /v1), media generation is a bespoke
submit→poll flow at /api/v1:

    POST /api/v1/model/generateImage   -> {"data": {"id": "<pred>"}}
    POST /api/v1/model/generateVideo   -> {"data": {"id": "<pred>"}}
    GET  /api/v1/model/prediction/{id} -> {"data": {"status": ..., "outputs": [...]}}
    POST /api/v1/model/uploadMedia     -> upload a ref image/video/audio, returns URL

Statuses: "processing" (in-flight) -> "completed" | "failed" (terminal).
`outputs` holds result URLs (image/video). Recommended poll interval ~5s.

The `model` field takes the FULL Atlas slug INCLUDING the operation, e.g.
"bytedance/seedance-2.0-mini/image-to-video". The model registry stores the
provider_id STEM ("bytedance/seedance-2.0-mini"); this backend appends the op
suffix from req.op via _OP_SUFFIX.

WARNING: request field names beyond {model, prompt, image_url} are UNVERIFIED
against a live key (the public docs only show minimal examples). All real-send
paths are dry-run safe; benchmark one call per modality before trusting cost.py.
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
from nazca.media import encode_image_data_uri, summarize_data_uri

if TYPE_CHECKING:
    from nazca.request import AudioRequest, ImageRequest, ThreeDRequest, VideoRequest

ATLAS_MEDIA_BASE = "https://api.atlascloud.ai/api/v1"  # media (async); LLM uses /v1

# nazca op -> Atlas slug operation suffix appended to the provider_id stem.
# (Image and video share this map; the endpoint chosen disambiguates modality.)
_OP_SUFFIX: dict[str, str] = {
    # image
    "t2i": "text-to-image",
    "i2i": "edit",
    "compose": "reference-to-image",
    "bg_remove": "remove-background",
    "style": "style-transfer",
    # video
    "t2v": "text-to-video",
    "i2v": "image-to-video",
    "ref2v": "reference-to-video",
    "v2v": "video-edit",
    "keyframe": "start-end-frame-to-video",
    "extend": "extend-video",
    "motion_control": "motion-control",
    "avatar": "avatar",  # Kling avatar; standalone avatar models override via _STANDALONE_STEMS
    # audio
    "tts": "text-to-speech",
    # 3d
    "t23d": "text-to-3d",
    "i23d": "image-to-3d",
}

# Ops whose model slug is STANDALONE (the stem is already the full slug, no operation
# suffix) — e.g. atlascloud/video-upscaler, kwaivgi/kling-effects.
_NO_SUFFIX_OPS: frozenset[str] = frozenset({"video_upscale", "effects"})

# Models whose stem IS the complete slug regardless of op (no operation suffix),
# e.g. the standalone avatar/talking-head models.
_STANDALONE_STEMS: frozenset[str] = frozenset({
    "atlascloud/video-upscaler",
    "kwaivgi/kling-effects",
    "atlascloud/infinitetalk",
    "bytedance/avatar-omni-human-v1.5",
    "xai/tts-v1",
})


def _model_slug(stem: str, op: str | None, default: str) -> str:
    """Compose the Atlas `model` value: stem + operation suffix.

    Some Atlas models bake resolution/variant into the stem instead of using an op
    suffix (e.g. ``bytedance/seedance-v1-pro-t2v-1080p``), and some are standalone
    (``atlascloud/video-upscaler``); for those the stem is already the full slug.
    """
    # Standalone slugs, standalone-slug ops, resolution-baked slugs, or slugs already
    # carrying a "*-to-*" operation token are passed through unchanged.
    if stem in _STANDALONE_STEMS or op in _NO_SUFFIX_OPS:
        return stem
    if stem.rsplit("/", 1)[-1].count("-to-") or stem.endswith(("-1080p", "-720p", "-480p")):
        return stem
    suffix = _OP_SUFFIX.get(op or "", default)
    return f"{stem}/{suffix}"


class AtlasError(BackendError):
    """Raised when an Atlas Cloud request fails (missing key, HTTP error, timeout)."""


class AtlasRateLimitError(AtlasError, _SharedRateLimitError):
    """429/503 that persisted past NAZCA_MAX_RETRIES retries.

    Distinct type so batch logic can tell "paced wrong" from a real failure.
    """


class AtlasBackend(Backend):
    name = "atlas"

    # ------------------------------------------------------------------ endpoints
    def image_endpoint(self) -> str:
        return f"{ATLAS_MEDIA_BASE}/model/generateImage"

    def video_endpoint(self) -> str:
        return f"{ATLAS_MEDIA_BASE}/model/generateVideo"

    def audio_endpoint(self) -> str:
        return f"{ATLAS_MEDIA_BASE}/model/generateAudio"  # verify endpoint name

    def threed_endpoint(self) -> str:
        return f"{ATLAS_MEDIA_BASE}/model/generate3D"  # verify endpoint name

    def encode_image_data_uri(self, path, max_edge: int | None = None) -> str:
        """Atlas takes ref images as data URIs (or uploaded URLs); verify per model."""
        return encode_image_data_uri(path, max_edge=max_edge)

    # ------------------------------------------------------------------ auth/http
    def auth_token(self) -> str:
        """Read ATLAS_API_KEY (env > config file) lazily — never called during dry-run."""
        key = config.ATLAS_API_KEY
        if not key:
            raise AtlasError(
                "ATLAS_API_KEY is not set. Run `nazca login` (or `nazca config set "
                "atlas_api_key <key>`) to save it, or export ATLAS_API_KEY for this "
                "session. Get a key at https://www.atlascloud.ai/console/api-keys"
            )
        return key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.auth_token()}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> dict:
        url = f"{ATLAS_MEDIA_BASE}{path}"
        return retry.post_json(
            url,
            body,
            headers=self._headers(),
            timeout=30,
            on_http_error=lambda code, detail: AtlasError(
                f"Atlas HTTP {code}: {detail}{hint('atlas', code, detail)}"
            ),
            on_rate_limited=lambda code, detail: AtlasRateLimitError(
                f"Atlas rate limit (HTTP {code}) persisted after retries: {detail}"
            ),
        )

    def _get(self, path: str) -> dict:
        url = f"{ATLAS_MEDIA_BASE}{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise AtlasError(
                f"Atlas HTTP {exc.code}: {detail}{hint('atlas', exc.code, detail)}"
            ) from exc

    def _poll(self, pred_id: str, download_timeout: int) -> bytes:
        """Poll prediction until terminal, then download the first output URL."""
        for _ in range(config.POLL_MAX_TRIES):
            time.sleep(config.POLL_INTERVAL)
            data = self._get(f"/model/prediction/{pred_id}").get("data", {})
            status = data.get("status")
            if status == "completed":
                outputs = data.get("outputs") or []
                if not outputs:
                    raise AtlasError(f"Atlas prediction {pred_id} completed with no outputs: {data}")
                out = outputs[0]
                with urllib.request.urlopen(out, timeout=download_timeout) as r:  # noqa: S310
                    return r.read()
            if status == "failed":
                raise AtlasError(f"Atlas prediction {pred_id} failed: {data.get('error', data)}")
            # else: "processing" / unknown -> keep polling
        raise AtlasError(f"Atlas prediction {pred_id} timed out after {config.POLL_MAX_TRIES} polls")

    # ------------------------------------------------------------------ run seam
    def run_image(self, model_id, api, region, req: ImageRequest):
        """Async image generate/edit. Schema beyond {model,prompt} UNVERIFIED."""
        # Infer the op from refs when the caller didn't set one (the CLI only forces
        # `op` for ops it can't infer, e.g. style — see generate_image).
        op = req.op or ("compose" if len(req.refs) > 1 else "i2i" if req.refs else "t2i")
        slug = _model_slug(model_id, op, "text-to-image")
        body: dict = {"model": slug, "prompt": req.prompt}
        if req.size:
            body["size"] = req.size  # verify field name per model
        if req.aspect_ratio:
            body["aspect_ratio"] = req.aspect_ratio  # verify
        if req.refs:
            encoded = [self.encode_image_data_uri(r, max_edge=2048) for r in req.refs]
            body["image_url"] = encoded[0] if len(encoded) == 1 else encoded  # verify (image_url vs images)

        if req.dry_run:
            plan = dict(body)
            if "image_url" in plan:
                iu = plan["image_url"]
                plan["image_url"] = (
                    summarize_data_uri(iu) if isinstance(iu, str)
                    else [summarize_data_uri(x) for x in iu]
                )
            return {
                "url": self.image_endpoint(),
                "model": slug,
                "backend": self.name,
                "api": api,
                "refs": len(req.refs),
                "est_cost_usd": req.est_cost_usd,
                "body": plan,
            }

        resp = self._post("/model/generateImage", body)
        pred_id = resp.get("data", {}).get("id")
        if not pred_id:
            raise AtlasError(f"No prediction id in response: {resp}")
        return self._poll(pred_id, download_timeout=60)

    def run_video(self, model_id, region, req: VideoRequest):
        """Async video generate/edit. Schema beyond {model,prompt,image_url} UNVERIFIED."""
        # Infer the op when the caller didn't set one: keyframe (start+end) > i2v
        # (start) > t2v. Source-video edit ops (motion_control/video_upscale) and
        # ref2v/effects arrive with req.op already set by the CLI.
        op = req.op or ("keyframe" if req.start and req.end else "i2v" if req.start else "t2v")
        slug = _model_slug(model_id, op, "text-to-video")
        body: dict = {
            "model": slug,
            "prompt": req.prompt,
            "duration": int(req.duration),
            "aspect_ratio": req.aspect_ratio,
            "resolution": req.resolution,  # verify field name
        }
        if req.source:  # source-video edit ops (motion_control / video_upscale): URL, not inlined
            body["video_url"] = req.source  # verify field name
        if req.start:
            body["image_url"] = self.encode_image_data_uri(req.start, max_edge=1280)  # verify
        if req.end:
            body["end_image_url"] = self.encode_image_data_uri(req.end, max_edge=1280)  # verify
        if req.refs:
            body["reference_images"] = [
                self.encode_image_data_uri(r, max_edge=1280) for r in req.refs
            ]  # verify field name
        if req.audio_path:  # avatar / lip-sync driving audio (real send: uploadMedia → URL)
            body["audio_url"] = req.audio_path  # verify field name

        if req.dry_run:
            preview = dict(body)
            for k in ("image_url", "end_image_url"):
                v = preview.get(k)
                if isinstance(v, str) and v.startswith("data:"):
                    preview[k] = summarize_data_uri(v)
            if "reference_images" in preview:
                preview["reference_images"] = [summarize_data_uri(x) for x in preview["reference_images"]]
            return {
                "url": self.video_endpoint(),
                "model": slug,
                "backend": self.name,
                "est_cost_usd": getattr(req, "est_cost_usd", None),
                "body": preview,
            }

        resp = self._post("/model/generateVideo", body)
        pred_id = resp.get("data", {}).get("id")
        if not pred_id:
            raise AtlasError(f"No prediction id in response: {resp}")
        return self._poll(pred_id, download_timeout=120)

    def run_audio(self, model_id, req: AudioRequest):
        """Async text-to-speech. Endpoint + schema UNVERIFIED → dry-run safe."""
        slug = _model_slug(model_id, req.op or "tts", "text-to-speech")
        body: dict = {"model": slug, "text": req.text}
        if req.voice:
            body["voice"] = req.voice  # verify field name
        if req.output_format:
            body["format"] = req.output_format  # verify field name

        if req.dry_run:
            return {
                "url": self.audio_endpoint(),
                "model": slug,
                "backend": self.name,
                "est_cost_usd": req.est_cost_usd,
                "body": dict(body),
            }

        resp = self._post("/model/generateAudio", body)
        pred_id = resp.get("data", {}).get("id")
        if not pred_id:
            raise AtlasError(f"No prediction id in response: {resp}")
        return self._poll(pred_id, download_timeout=60)

    def run_3d(self, model_id, req: ThreeDRequest):
        """Async text/image → 3D (GLB). Endpoint + schema UNVERIFIED → dry-run safe."""
        slug = _model_slug(model_id, req.op or "t23d", "text-to-3d")
        body: dict = {"model": slug}
        if req.prompt:
            body["prompt"] = req.prompt
        if req.source:  # i23d: input image
            body["image_url"] = self.encode_image_data_uri(req.source, max_edge=1280)  # verify

        if req.dry_run:
            preview = dict(body)
            if isinstance(preview.get("image_url"), str) and preview["image_url"].startswith("data:"):
                preview["image_url"] = summarize_data_uri(preview["image_url"])
            return {
                "url": self.threed_endpoint(),
                "model": slug,
                "backend": self.name,
                "est_cost_usd": req.est_cost_usd,
                "body": preview,
            }

        resp = self._post("/model/generate3D", body)
        pred_id = resp.get("data", {}).get("id")
        if not pred_id:
            raise AtlasError(f"No prediction id in response: {resp}")
        return self._poll(pred_id, download_timeout=120)
