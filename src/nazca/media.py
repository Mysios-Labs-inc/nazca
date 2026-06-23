"""Image codec helpers — encode, scale, and serialize images to b64/bytes/data-URIs.

Single canonical implementation used across all backends (Vertex, fal, OpenAI, ModelArk).
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

# Reference image max dimension (Vertex/fal/ModelArk refs)
MAX_REF_EDGE = 2048

# Thumbnail/start-frame max dimension (video generation)
MAX_THUMB_EDGE = 1280


def encode_image_b64(path: str | Path, max_edge: int | None = None, fmt: str = "JPEG") -> tuple[str, str]:
    """Return (base64, mime) for an image, optionally downscaled to max_edge.

    Args:
        path: Image file path.
        max_edge: Max dimension (width or height) after downscaling; None = no downscale.
        fmt: Output format ("JPEG" or "PNG"); affects MIME type and color space.

    Returns:
        (base64_string, mime_type) — e.g., ("data:...", "image/jpeg").
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


def encode_image_data_uri(path: str | Path, max_edge: int | None = None) -> str:
    """Return a data-URI string for an image (PNG, optionally downscaled to max_edge).

    Used by fal and ModelArk backends, which expect inputs as data:image/png;base64,...

    Args:
        path: Image file path.
        max_edge: Max dimension (width or height) after downscaling; None = no downscale.

    Returns:
        data-URI string, e.g., "data:image/png;base64,iVBOR...".
    """
    b64, mime = encode_image_b64(path, max_edge=max_edge, fmt="PNG")
    return f"data:{mime};base64,{b64}"


def encode_image_bytes(path: str | Path, max_edge: int | None = None, fmt: str = "PNG") -> bytes:
    """Read an image, optionally downscale to max_edge, return raw format bytes.

    Used by OpenAI Images multipart upload (which requires raw PNG bytes, not b64).

    Args:
        path: Image file path.
        max_edge: Max dimension (width or height) after downscaling; None = no downscale.
        fmt: Output format ("PNG" or "JPEG"); default "PNG" for multipart/form-data.

    Returns:
        Raw image bytes in the requested format.
    """
    img = Image.open(path)
    img = img.convert("RGBA") if fmt == "PNG" else img.convert("RGB")
    if max_edge:
        w, h = img.size
        scale = min(1.0, max_edge / max(w, h))
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()
