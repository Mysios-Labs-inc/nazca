"""Shared Vertex AI plumbing — gcloud auth + REST, used by image and video.

One auth path for everything: `gcloud auth print-access-token` against the
configured project. No API keys, no provider SDKs.
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from mediagen import config


class VertexError(RuntimeError):
    pass


def gcloud_token() -> str:
    try:
        out = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError as e:
        raise VertexError("gcloud not found — install the Google Cloud SDK") from e
    except subprocess.CalledProcessError as e:
        raise VertexError(f"gcloud auth failed: {e.stderr.strip()}") from e
    return out.stdout.strip()


def model_base(model: str, location: str | None = None) -> str:
    loc = location or config.VERTEX_LOCATION
    return (
        f"https://{loc}-aiplatform.googleapis.com/v1/projects/"
        f"{config.VERTEX_PROJECT}/locations/{loc}/publishers/google/models/{model}"
    )


def post(url: str, body: dict, token: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted Google endpoint)
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        raise VertexError(f"HTTP {e.code} from Vertex: {detail}") from e


def encode_image_b64(path: str | Path, max_edge: int | None = None, fmt: str = "JPEG") -> tuple[str, str]:
    """Return (base64, mime) for an image, optionally downscaled to max_edge."""
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
