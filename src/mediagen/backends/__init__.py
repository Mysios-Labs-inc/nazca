"""Provider-agnostic backend seam.

Each backend (today only `vertex`) knows how to mint its own credential, build
an endpoint URL, and POST a request. Auth is lazy: a backend only mints its
credential when one of *its* models is actually dispatched — so a Vertex-only
run never reaches for any other provider's key (BYOK stays opt-in).

Future backends (fal, modelark) are additive: implement `Backend` and add one
key to `BACKENDS`.
"""

from __future__ import annotations

from mediagen.backends.base import Backend
from mediagen.backends.fal import FalBackend
from mediagen.backends.modelark import ModelArkBackend
from mediagen.backends.vertex import VertexBackend

# backend name -> implementation. Add one key per provider here.
BACKENDS: dict[str, Backend] = {
    "vertex": VertexBackend(),
    "fal": FalBackend(),
    "modelark": ModelArkBackend(),
}


def get_backend(name: str) -> Backend:
    try:
        return BACKENDS[name]
    except KeyError as e:
        raise ValueError(f"unknown backend '{name}' (have: {', '.join(BACKENDS)})") from e


__all__ = ["Backend", "FalBackend", "ModelArkBackend", "VertexBackend", "BACKENDS", "get_backend"]
