"""Environment + defaults. Vertex AI is the default auth path (no API keys).
Opting into a non-Google backend (e.g. fal) requires its own key — see below.

Override any value via env var. Fallback: ~/.config/nazca/config.ini (see credstore).

Settings are resolved FRESH on every attribute access (PEP 562 __getattr__).
An explicit write — e.g. ``config.VERTEX_PROJECT = project`` in setup.py, or
``monkeypatch.setattr(config, "VERTEX_PROJECT", ...)`` in tests — creates a real
module attribute that *shadows* __getattr__, so those paths are unaffected.
"""

from __future__ import annotations

import os

from nazca.credstore import get_value

#: Names exported by this module (lets static tools see them without eager assignment).
#: F822 is suppressed because these names are provided dynamically via __getattr__
#: (PEP 562) and are not assigned at module level by design.
__all__ = [  # noqa: F822
    "VERTEX_PROJECT",
    "VERTEX_LOCATION",
    "VEO_MODEL",
    "POLL_INTERVAL",
    "POLL_MAX_TRIES",
    "FAL_KEY",
    "ARK_API_KEY",
    "OPENAI_API_KEY",
]


def __getattr__(name: str):  # noqa: ANN001, ANN201
    """Resolve dynamic settings fresh on every access (PEP 562).

    Called only when *name* is NOT already a real module attribute, so explicit
    writes (setup.py) and test monkeypatches always take precedence.
    """
    # --- Vertex AI (project + region used for BOTH image and video) ---
    # No hardcoded project: set your own via `nazca setup`, the VERTEX_PROJECT env
    # var, or config.ini. Precedence: env var > config.ini > unset (clear error at
    # call time, see backends/vertex.py). Region defaults to us-central1.
    if name == "VERTEX_PROJECT":
        return os.getenv("VERTEX_PROJECT") or get_value("vertex_project")
    if name == "VERTEX_LOCATION":
        return os.getenv("VERTEX_LOCATION") or get_value("vertex_location") or "us-central1"

    # --- Models ---
    if name == "VEO_MODEL":
        return os.getenv("VEO_MODEL", "veo-3.1-fast-generate-001")

    # --- Video poll cadence (shared by Vertex LRO polling AND fal queue polling) ---
    if name == "POLL_INTERVAL":
        return int(os.getenv("VEO_POLL_INTERVAL", "15"))
    if name == "POLL_MAX_TRIES":
        return int(os.getenv("VEO_POLL_MAX_TRIES", "60"))

    # --- fal.ai (optional — only required when a fal model is selected) ---
    # Precedence: FAL_KEY env var > ~/.config/nazca/config.ini > None
    # Keep keys in your shell profile, secrets manager, or `nazca login`.
    if name == "FAL_KEY":
        return os.getenv("FAL_KEY") or get_value("fal_key")

    # --- ByteDance ModelArk (optional — only required when a modelark model is selected) ---
    # Precedence: ARK_API_KEY env var > ~/.config/nazca/config.ini > None
    if name == "ARK_API_KEY":
        return os.getenv("ARK_API_KEY") or get_value("ark_api_key")

    # --- OpenAI Images (optional — only required when an openai model is selected) ---
    # Precedence: OPENAI_API_KEY env var > ~/.config/nazca/config.ini > None
    if name == "OPENAI_API_KEY":
        return os.getenv("OPENAI_API_KEY") or get_value("openai_api_key")

    raise AttributeError(name)
