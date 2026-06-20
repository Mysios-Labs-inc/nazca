"""Vertex AI backend — gcloud auth + REST.

The original shared Vertex plumbing, moved here behind the `Backend` interface.
One auth path for everything: `gcloud auth print-access-token` against the
configured project. No API keys, no provider SDKs.

The module-level functions (`gcloud_token`, `model_base`, `post`,
`encode_image_b64`) are kept as the implementation and re-exported by the
top-level `nazca.vertex` shim for back-compat.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from nazca import config
from nazca.backends.base import Backend


class VertexError(RuntimeError):
    pass


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
    loc = location or config.VERTEX_LOCATION
    host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/projects/"
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


class VertexBackend(Backend):
    """Vertex AI: gcloud OAuth token + publishers/google/models REST."""

    name = "vertex"

    def auth_token(self) -> str:
        return access_token()

    def build_url(self, model: str, op: str, location: str | None = None) -> str:
        return f"{model_base(model, location)}:{op}"

    def post(self, url: str, body: dict, token: str) -> dict:
        return post(url, body, token)

    def encode_image_b64(
        self, path: str | Path, max_edge: int | None = None, fmt: str = "JPEG"
    ) -> tuple[str, str]:
        return encode_image_b64(path, max_edge=max_edge, fmt=fmt)
