"""Back-compat shim — Vertex plumbing now lives in `mediagen.backends.vertex`.

Kept so `from mediagen.vertex import gcloud_token, model_base, post,
encode_image_b64, VertexError` keeps working. New code should use the backend
seam (`mediagen.backends`).
"""

from __future__ import annotations

from mediagen.backends.vertex import (
    VertexError,
    encode_image_b64,
    gcloud_token,
    model_base,
    post,
)

__all__ = ["VertexError", "encode_image_b64", "gcloud_token", "model_base", "post"]
