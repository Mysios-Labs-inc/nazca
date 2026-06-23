"""Back-compat shim — Vertex plumbing now lives in `nazca.backends.vertex`.

Kept so `from nazca.vertex import gcloud_token, model_base, post,
encode_image_b64, VertexError` keeps working. New code should use the backend
seam (`nazca.backends`).
"""

from __future__ import annotations

from nazca.backends.vertex import (
    RateLimitError,
    VertexError,
    access_token,
    gcloud_token,
    model_base,
    post,
)
from nazca.media import encode_image_b64

__all__ = [
    "RateLimitError",
    "VertexError",
    "access_token",
    "encode_image_b64",
    "gcloud_token",
    "model_base",
    "post",
]
