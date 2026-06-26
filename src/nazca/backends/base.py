"""Backend interface — what every provider must expose.

A lightweight base class (not a pure Protocol) that unifies common provider
plumbing — credential handling, endpoint building, HTTP mechanics, image encoding —
so all backends can be type-checked and registered uniformly. Each method mirrors
a piece of the original single-provider `vertex.py` infrastructure.

The load-bearing seam is `run_image` / `run_video`: each backend owns its own
body-building, dispatch, extraction, and dry-run plan rendering, so the call sites
in `image.py` / `video.py` collapse to a single `backend.run_image(...)` call with
no per-backend branching.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nazca.request import AudioRequest, ImageRequest, ThreeDRequest, VideoRequest


class Backend:
    """Provider plumbing: credential + endpoint + HTTP, plus image (de)coding."""

    name: str = "base"

    def auth_token(self) -> str:
        """Mint/return this backend's credential. Called only on dispatch (lazy)."""
        raise NotImplementedError

    def build_url(self, model: str, op: str, location: str | None = None) -> str:
        """Endpoint for `model` and operation `op` (e.g. generateContent, predict)."""
        raise NotImplementedError

    def post(self, url: str, body: dict, token: str) -> dict:
        """HTTP POST a JSON body, return decoded JSON."""
        raise NotImplementedError

    def encode_image_b64(
        self, path: str | Path, max_edge: int | None = None, fmt: str = "JPEG"
    ) -> tuple[str, str]:
        """Return (base64, mime) for an image, optionally downscaled to max_edge."""
        raise NotImplementedError

    # --------------------------------------------------------------- run seam

    def run_image(
        self, model_id: str, api: str, region: str | None, req: ImageRequest
    ) -> bytes | dict:
        """Generate (or modify) one image with the resolved `model_id`.

        `api` and `region` are the resolved routing fields (sub-API within the
        backend, and provider region for Vertex). Returns raw image bytes on a real
        run, or the dry-run plan dict when ``req.dry_run`` is set. Backends that do
        not do images raise.
        """
        raise NotImplementedError(f"backend '{self.name}' does not support images")

    def run_video(
        self, model_id: str, region: str | None, req: VideoRequest
    ) -> bytes | dict:
        """Generate (or edit) one video clip with the resolved `model_id`.

        Returns raw video bytes on a real run, or the dry-run plan dict when
        ``req.dry_run`` is set. Backends that do not do video raise.
        """
        raise NotImplementedError(f"backend '{self.name}' does not support video")

    def run_audio(self, model_id: str, req: AudioRequest) -> bytes | dict:
        """Synthesize one audio clip (text-to-speech) with the resolved `model_id`.

        Returns raw audio bytes on a real run, or the dry-run plan dict when
        ``req.dry_run`` is set. Backends that do not do audio raise.
        """
        raise NotImplementedError(f"backend '{self.name}' does not support audio")

    def run_3d(self, model_id: str, req: ThreeDRequest) -> bytes | dict:
        """Generate one 3D asset (GLB mesh) with the resolved `model_id`.

        Returns raw GLB bytes on a real run, or the dry-run plan dict when
        ``req.dry_run`` is set. Backends that do not do 3D raise.
        """
        raise NotImplementedError(f"backend '{self.name}' does not support 3D")
