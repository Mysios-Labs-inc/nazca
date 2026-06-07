"""Environment + defaults. Override any value via env var."""

from __future__ import annotations

import os

# --- Vertex Veo (video) ---
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "florece-492623")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-fast-generate-001")

# --- Image providers ---
FAL_KEY = os.getenv("FAL_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

# Video poll cadence
POLL_INTERVAL = int(os.getenv("VEO_POLL_INTERVAL", "15"))
POLL_MAX_TRIES = int(os.getenv("VEO_POLL_MAX_TRIES", "60"))
