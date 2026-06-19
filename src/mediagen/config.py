"""Environment + defaults. Vertex AI is the default auth path (no API keys).
Opting into a non-Google backend (e.g. fal) requires its own key — see below.

Override any value via env var. Fallback: ~/.config/mediagen/config.ini (see credstore).
"""

from __future__ import annotations

import os

from mediagen.credstore import get_value

# --- Vertex AI (project + region used for BOTH image and video) ---
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "florece-492623")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")

# --- Models ---
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-fast-generate-001")

# --- Video poll cadence (shared by Vertex LRO polling AND fal queue polling) ---
POLL_INTERVAL = int(os.getenv("VEO_POLL_INTERVAL", "15"))
POLL_MAX_TRIES = int(os.getenv("VEO_POLL_MAX_TRIES", "60"))

# --- fal.ai (optional — only required when a fal model is selected) ---
# Precedence: FAL_KEY env var > ~/.config/mediagen/config.ini > None
# Keep keys in your shell profile, secrets manager, or `mediagen login`.
FAL_KEY: str | None = os.getenv("FAL_KEY") or get_value("fal_key")

# --- ByteDance ModelArk (optional — only required when a modelark model is selected) ---
# Precedence: ARK_API_KEY env var > ~/.config/mediagen/config.ini > None
ARK_API_KEY: str | None = os.getenv("ARK_API_KEY") or get_value("ark_api_key")
