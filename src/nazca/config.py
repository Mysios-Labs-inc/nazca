"""Environment + defaults. Vertex AI is the default auth path (no API keys).
Opting into a non-Google backend (e.g. fal) requires its own key — see below.

Override any value via env var. Fallback: ~/.config/nazca/config.ini (see credstore).
"""

from __future__ import annotations

import os

from nazca.credstore import get_value

# --- Vertex AI (project + region used for BOTH image and video) ---
# No default project: you must set VERTEX_PROJECT to your own GCP project. A
# clear error is raised at call time if it's unset (see backends/vertex.py).
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")

# --- Models ---
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-fast-generate-001")

# --- Video poll cadence (shared by Vertex LRO polling AND fal queue polling) ---
POLL_INTERVAL = int(os.getenv("VEO_POLL_INTERVAL", "15"))
POLL_MAX_TRIES = int(os.getenv("VEO_POLL_MAX_TRIES", "60"))

# --- fal.ai (optional — only required when a fal model is selected) ---
# Precedence: FAL_KEY env var > ~/.config/nazca/config.ini > None
# Keep keys in your shell profile, secrets manager, or `nazca login`.
FAL_KEY: str | None = os.getenv("FAL_KEY") or get_value("fal_key")

# --- ByteDance ModelArk (optional — only required when a modelark model is selected) ---
# Precedence: ARK_API_KEY env var > ~/.config/nazca/config.ini > None
ARK_API_KEY: str | None = os.getenv("ARK_API_KEY") or get_value("ark_api_key")
