"""Backend interface — what every provider must expose.

A lightweight base class (not a pure Protocol) so backends can share nothing but
still be type-checked and registered uniformly. Each method mirrors a piece of
the original single-provider `vertex.py` plumbing.
"""

from __future__ import annotations

from pathlib import Path


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
