"""ByteDance ModelArk direct backend (Seedream image + Seedance video).

WARNING: ModelArk API IDs, endpoints, and schema are UNVERIFIED (dry-run only).
Benchmark against fal before real spend — the ~25% cheaper claim is unverified.
"""

import json
import time
import urllib.error
import urllib.request

from nazca import config
from nazca.backends.base import Backend
from nazca.backends.vertex import encode_image_b64

ARK_BASE = "https://ark.ap-southeast.bytepluses.com/api/v3"  # verify against ModelArk docs


class ModelArkError(Exception):
    pass


class ModelArkBackend(Backend):
    name = "modelark"

    def image_endpoint(self) -> str:
        return f"{ARK_BASE}/images/generations"  # verify against ModelArk docs

    def video_endpoint(self) -> str:
        return f"{ARK_BASE}/contents/generations/tasks"  # verify against ModelArk docs

    def encode_image_b64(self, path, max_edge=None, fmt="PNG"):
        return encode_image_b64(path, max_edge=max_edge, fmt=fmt)

    def encode_image_data_uri(self, path, max_edge: int | None = None) -> str:
        """ModelArk takes image inputs as data URIs (verify against docs)."""
        b64, mime = self.encode_image_b64(path, max_edge=max_edge, fmt="PNG")
        return f"data:{mime};base64,{b64}"

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
        url = f"{ARK_BASE}{path}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise ModelArkError(f"ModelArk HTTP {exc.code}: {exc.read().decode()[:400]}") from exc

    def _get(self, path: str) -> dict:
        url = f"{ARK_BASE}{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise ModelArkError(f"ModelArk HTTP {exc.code}: {exc.read().decode()[:400]}") from exc

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
