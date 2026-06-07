"""Environment + defaults. One auth path: Vertex AI via gcloud. No API keys.

Override any value via env var.
"""

from __future__ import annotations

import os

# --- Vertex AI (project + region used for BOTH image and video) ---
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "your-gcp-project")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")

# --- Models ---
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-fast-generate-001")

# --- Video poll cadence ---
POLL_INTERVAL = int(os.getenv("VEO_POLL_INTERVAL", "15"))
POLL_MAX_TRIES = int(os.getenv("VEO_POLL_MAX_TRIES", "60"))
