"""Provider-agnostic backend seam.

Backends (vertex, fal, modelark, openai, atlas) each know how to mint their own
credential, build an endpoint URL, and POST a request. Auth is lazy: a backend
only mints its credential when one of *its* models is actually dispatched — so a
run using only one provider never reaches for any other provider's key (BYOK
stays opt-in).

Adding new backends remains additive: implement `Backend` and add one key to
`BACKENDS`.
"""

from __future__ import annotations

from nazca.backends.atlas import AtlasBackend
from nazca.backends.base import Backend
from nazca.backends.fal import FalBackend
from nazca.backends.modelark import ModelArkBackend
from nazca.backends.openai import OpenAIBackend
from nazca.backends.vertex import VertexBackend

# backend name -> implementation. Add one key per provider here.
BACKENDS: dict[str, Backend] = {
    "vertex": VertexBackend(),
    "fal": FalBackend(),
    "modelark": ModelArkBackend(),
    "openai": OpenAIBackend(),
    "atlas": AtlasBackend(),
}


def get_backend(name: str) -> Backend:
    try:
        return BACKENDS[name]
    except KeyError as e:
        raise ValueError(f"unknown backend '{name}' (have: {', '.join(BACKENDS)})") from e


__all__ = [
    "Backend",
    "AtlasBackend",
    "FalBackend",
    "ModelArkBackend",
    "OpenAIBackend",
    "VertexBackend",
    "BACKENDS",
    "get_backend",
]
