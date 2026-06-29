"""Vertex AI backend — gcloud auth + REST.

The original shared Vertex plumbing, moved here behind the `Backend` interface.
One auth path for everything: `gcloud auth print-access-token` against the
configured project. No API keys, no provider SDKs.

The module-level functions (`gcloud_token`, `model_base`, `post`,
`access_token`) are the implementation; import them from here directly.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from nazca import config, retry
from nazca.backends.base import Backend
from nazca.errors import BackendError, ImageError, VeoError
from nazca.errors import RateLimitError as _SharedRateLimitError
from nazca.log import get_logger
from nazca.media import encode_image_b64

logger = get_logger("backends.vertex")

if TYPE_CHECKING:
    from nazca.request import ImageRequest, VideoRequest


class VertexError(BackendError):
    """Raised when a Vertex AI request fails (auth error, HTTP error, etc.)."""


class RateLimitError(VertexError, _SharedRateLimitError):
    """429/503/RESOURCE_EXHAUSTED that persisted past NAZCA_MAX_RETRIES retries.

    A distinct type so batch logic can tell "paced wrong" from a real failure.
    Inherits from both ``VertexError`` and the shared ``nazca.errors.RateLimitError``
    so callers can catch either.
    """


# Common Google Cloud SDK install locations, checked when `gcloud` is not on
# PATH. This matters when nazca runs as an MCP server: Claude Desktop spawns the
# server detached with a minimal PATH that excludes the SDK's bin dir, so a bare
# "gcloud" lookup fails even though the SDK is installed. Set GCLOUD_BIN to point
# at the binary explicitly if your install lives elsewhere.
_GCLOUD_FALLBACK_PATHS = (
    "~/google-cloud-sdk/bin/gcloud",
    "/opt/homebrew/bin/gcloud",
    "/usr/local/bin/gcloud",
    "/usr/lib/google-cloud-sdk/bin/gcloud",
    "/snap/bin/gcloud",
)


def _find_gcloud() -> str:
    """Locate the gcloud binary, tolerant of a minimal PATH (e.g. MCP subprocess).

    Order: $GCLOUD_BIN → PATH lookup → common SDK install locations.
    """
    explicit = os.getenv("GCLOUD_BIN")
    if explicit and Path(explicit).expanduser().is_file():
        return str(Path(explicit).expanduser())
    found = shutil.which("gcloud")
    if found:
        return found
    for candidate in _GCLOUD_FALLBACK_PATHS:
        p = Path(candidate).expanduser()
        if p.is_file():
            return str(p)
    raise VertexError(
        "gcloud not found — install the Google Cloud SDK, or set GCLOUD_BIN to "
        "the gcloud binary (e.g. when running under Claude Desktop with a minimal PATH)"
    )


_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class _UrllibResponse:
    """Minimal google.auth transport Response backed by urllib (duck-typed)."""

    def __init__(self, status: int, headers: dict, data: bytes):
        self.status = status
        self.headers = headers
        self.data = data


class _UrllibRequest:
    """A google.auth transport using stdlib urllib — avoids a `requests` dependency.

    google.auth credential refresh only needs a callable that performs an HTTP
    request and returns an object exposing `.status` and `.data`.
    """

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        if isinstance(body, str):
            body = body.encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (Google token endpoint)
                return _UrllibResponse(resp.status, dict(resp.headers), resp.read())
        except urllib.error.HTTPError as e:
            return _UrllibResponse(e.code, dict(e.headers), e.read())


def _adc_token() -> str | None:
    """Mint a token from Application Default Credentials via the google-auth library.

    Returns None when google-auth is not installed (so the caller can fall back to
    the gcloud binary). Returns None when google-auth is present but finds no
    credentials — again deferring to the binary path, which also covers users who
    ran only `gcloud auth login` (a user-account login google-auth does not read).

    The advantage over shelling out to gcloud: no `gcloud` binary on PATH is
    required, so this works inside the Claude Desktop MCP subprocess.
    """
    try:
        import logging

        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
    except ImportError:
        return None
    # nazca supplies its own project (config.VERTEX_PROJECT) in the request URL, so
    # google-auth's "no quota project" warning is misleading noise — silence it.
    logging.getLogger("google.auth._default").setLevel(logging.ERROR)
    try:
        creds, _ = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
    except DefaultCredentialsError:
        return None
    creds.refresh(_UrllibRequest())
    return creds.token


def gcloud_token() -> str:
    """Mint a Vertex access token by shelling out to the gcloud binary."""
    gcloud = _find_gcloud()
    try:
        out = subprocess.run(
            [gcloud, "auth", "print-access-token"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        raise VertexError(f"gcloud auth failed: {e.stderr.strip()}") from e
    return out.stdout.strip()


def access_token() -> str:
    """Return a Vertex access token, preferring ADC (google-auth) over the binary.

    Order: google-auth ADC (no binary needed — works under the MCP subprocess) →
    gcloud binary (covers `gcloud auth login` user accounts). Raises with a pointer
    to `nazca setup` when neither yields a token.
    """
    token = _adc_token()
    if token:
        return token
    try:
        return gcloud_token()
    except VertexError as e:
        raise VertexError(
            f"{e}\nNo Google credentials found. Run `nazca setup` to install the "
            "Cloud SDK and authenticate (gcloud auth application-default login)."
        ) from e


def model_base(model: str, location: str | None = None) -> str:
    """Vertex model URL. Handles the `global` region (different host, no prefix)."""
    if not config.VERTEX_PROJECT:
        raise VertexError(
            "VERTEX_PROJECT is not set — point nazca at your own GCP project, e.g.\n"
            "  export VERTEX_PROJECT=my-gcp-project\n"
            "(or set it in your MCP server config's env block)."
        )
    loc = location or config.VERTEX_LOCATION
    host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/projects/"
        f"{config.VERTEX_PROJECT}/locations/{loc}/publishers/google/models/{model}"
    )


def post(url: str, body: dict, token: str) -> dict:
    """POST to Vertex with bounded backoff on 429/503/RESOURCE_EXHAUSTED (item 1A)."""
    return retry.post_json(
        url,
        body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        on_http_error=lambda code, detail: VertexError(f"HTTP {code} from Vertex: {detail}"),
        on_rate_limited=lambda code, detail: RateLimitError(
            f"Vertex rate limit (HTTP {code}) persisted after retries: {detail}"
        ),
    )




class VertexBackend(Backend):
    """Vertex AI: gcloud OAuth token + publishers/google/models REST."""

    name = "vertex"

    def auth_token(self) -> str:
        logger.debug("minting token")
        return access_token()

    def build_url(self, model: str, op: str, location: str | None = None) -> str:
        return f"{model_base(model, location)}:{op}"

    def _plan_url(self, model: str, op: str, location: str | None = None) -> str:
        """URL for a --dry-run plan. Tolerates an unset VERTEX_PROJECT: dry-run is
        documented as needing no credentials/config, so substitute a placeholder
        project instead of raising. Real dispatch keeps build_url's helpful error."""
        try:
            return self.build_url(model, op, location)
        except VertexError:
            loc = location or config.VERTEX_LOCATION
            host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
            return (
                f"https://{host}/v1/projects/<VERTEX_PROJECT>/locations/{loc}"
                f"/publishers/google/models/{model}:{op}"
            )

    def post(self, url: str, body: dict, token: str) -> dict:
        return post(url, body, token)

    def encode_image_b64(
        self, path: str | Path, max_edge: int | None = None, fmt: str = "JPEG"
    ) -> tuple[str, str]:
        # Re-export for the backend interface; actual implementation is in nazca.media
        return encode_image_b64(path, max_edge=max_edge, fmt=fmt)

    # ------------------------------------------------------------------ image

    def run_image(self, resolved, req: ImageRequest):
        """Gemini (:generateContent), Imagen (:predict), or VTO (:predict) — owns body + extract + plan."""
        model_id, api, region = resolved.provider_id, resolved.api, resolved.region
        if api == "imagen" and req.refs:
            raise ImageError(
                f"model '{model_id}' (imagen) is text-to-image only — drop --ref or use a nano-banana model"
            )

        if api == "imagen":
            op = "predict"
            body = self._imagen_body(req.prompt, req.aspect_ratio)
            extract = self._imagen_extract
        elif api == "vto":
            op = "predict"
            body = self._vto_body(req.source, req.refs)
            extract = self._imagen_extract  # same predictions[0].bytesBase64Encoded shape
        else:
            op = "generateContent"
            body = self._gemini_body(req.prompt, req.refs, req.aspect_ratio, req.size)
            extract = self._gemini_extract

        if req.dry_run:
            info: dict = {
                "url": self._plan_url(model_id, op, region),
                "model": model_id,
                "location": region,
                "api": api,
                "refs": len(req.refs),
                "size": req.size,
                "est_cost_usd": req.est_cost_usd,
            }
            if api == "imagen":
                info["parameters"] = body["parameters"]
            elif api == "vto":
                info["parameters"] = body["parameters"]
                info["products"] = len(req.refs)
            else:
                info["generationConfig"] = body["generationConfig"]
                info["parts"] = [
                    ({"inlineData": f"<{len(p['inlineData']['data'])} b64>"} if "inlineData" in p else p)
                    for p in body["contents"][0]["parts"]
                ]
            return info

        token = self.auth_token()
        resp = self.post(self.build_url(model_id, op, region), body, token)
        return extract(resp)

    @staticmethod
    def _gemini_body(prompt: str, refs: list[str], aspect_ratio: str | None, size: str | None) -> dict:
        parts: list[dict] = [{"text": prompt}]
        for r in refs:  # gemini-3-pro-image accepts up to 14 reference images
            b64, mime = encode_image_b64(r, max_edge=2048, fmt="PNG")
            parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        gen_cfg: dict = {"responseModalities": ["IMAGE"]}
        img_cfg: dict = {}
        if aspect_ratio:
            img_cfg["aspectRatio"] = aspect_ratio
        if size:
            # 1K/2K/4K — honored by gemini-3 image models; 2.5-flash-image ignores it (1K)
            img_cfg["imageSize"] = size
        if img_cfg:
            gen_cfg["imageConfig"] = img_cfg
        return {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}

    @staticmethod
    def _gemini_extract(resp: dict) -> bytes:
        return gemini_extract(resp)

    @staticmethod
    def _imagen_body(prompt: str, aspect_ratio: str | None) -> dict:
        params: dict = {"sampleCount": 1}
        if aspect_ratio:
            params["aspectRatio"] = aspect_ratio
        return {"instances": [{"prompt": prompt}], "parameters": params}

    @staticmethod
    def _vto_body(person: str, products: list[str]) -> dict:
        """Virtual Try-On :predict body — person image + up to 4 garment images, no prompt."""
        p_b64, _ = encode_image_b64(person, max_edge=2048, fmt="PNG")
        prod = []
        for g in products:
            g_b64, _ = encode_image_b64(g, max_edge=2048, fmt="PNG")
            prod.append({"image": {"bytesBase64Encoded": g_b64}})
        return {
            "instances": [{
                "personImage": {"image": {"bytesBase64Encoded": p_b64}},
                "productImages": prod,
            }],
            "parameters": {"sampleCount": 1},
        }

    @staticmethod
    def _imagen_extract(resp: dict) -> bytes:
        preds = resp.get("predictions") or []
        if not preds:
            raise ImageError(f"no prediction in imagen response: {str(resp)[:400]}")
        b64 = preds[0].get("bytesBase64Encoded")
        if not b64:
            raise ImageError(f"no image bytes in imagen prediction: {str(preds[0])[:300]}")
        return base64.b64decode(b64)


    # ------------------------------------------------------------------ video (Veo)

    def run_video(self, resolved, req: VideoRequest):
        """Veo predictLongRunning + poll — owns body + poll + extract + plan."""
        model_id = resolved.provider_id
        instance: dict = {"prompt": req.prompt}
        if req.start:  # omit `image` for text-to-video
            start_b64, mime = self.encode_image_b64(req.start, max_edge=1280, fmt="JPEG")
            instance["image"] = {"bytesBase64Encoded": start_b64, "mimeType": mime}
        if req.end:
            end_b64, emime = self.encode_image_b64(req.end, max_edge=1280, fmt="JPEG")
            instance["lastFrame"] = {"bytesBase64Encoded": end_b64, "mimeType": emime}

        body = {
            "instances": [instance],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": req.aspect_ratio,
                "resolution": req.resolution,
                "durationSeconds": int(req.duration),
                "generateAudio": req.audio,
            },
        }

        if req.dry_run:
            preview = json.loads(json.dumps(body))
            for inst in preview["instances"]:
                for k in ("image", "lastFrame"):
                    if k in inst:
                        inst[k]["bytesBase64Encoded"] = f"<{len(instance[k]['bytesBase64Encoded'])} b64 chars>"
            return {"url": self._plan_url(model_id, "predictLongRunning"), **preview}

        token = self.auth_token()
        submit = self.post(self.build_url(model_id, "predictLongRunning"), body, token)
        op = submit.get("name")
        if not op:
            raise VeoError(f"submit failed: {json.dumps(submit)[:500]}")

        for attempt in range(1, config.POLL_MAX_TRIES + 1):
            time.sleep(config.POLL_INTERVAL)
            poll = self.post(
                self.build_url(model_id, "fetchPredictOperation"), {"operationName": op}, token
            )
            status = "done" if poll.get("done") else "pending"
            logger.info(f"poll attempt {attempt}/{config.POLL_MAX_TRIES}: {status}")
            if poll.get("done"):
                break
        else:
            raise VeoError("timed out waiting for video generation")

        if poll.get("error"):
            raise VeoError(f"generation error: {poll['error'].get('message')}")
        resp = poll.get("response", {})
        vids = resp.get("videos") or resp.get("generatedSamples") or []
        if not vids:
            raise VeoError(f"no video in response: {json.dumps(resp)[:500]}")
        v = vids[0]
        b64 = v.get("bytesBase64Encoded") or v.get("video", {}).get("bytesBase64Encoded")
        if not b64:
            gcs = v.get("gcsUri")
            if gcs:
                raise VeoError(f"stored at {gcs} (no inline bytes) — fetch with gsutil cp")
            raise VeoError(f"unrecognized video payload: {json.dumps(v)[:300]}")
        return base64.b64decode(b64)


# ---------------------------------------------------------------------------
# Public helpers — importable from nazca.backends.vertex
# ---------------------------------------------------------------------------


def gemini_extract(resp: dict) -> bytes:
    """Extract raw image bytes from a Gemini generateContent response dict.

    Exposed as a module-level function so callers outside this module (e.g.
    vertex_batch) can import it without reaching into image.py internals.
    """
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    raise ImageError(f"no image part in response: {str(resp)[:400]}")
