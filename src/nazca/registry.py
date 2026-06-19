"""User-override model registry — stdlib only (json, os, pathlib).

Override file: $XDG_CONFIG_HOME/nazca/models.json
               (or ~/.config/nazca/models.json when XDG_CONFIG_HOME is unset)

Schema
------
{
  "image": {
    "<shorthand>": {
      "id":      "<raw provider model id>",
      "backend": "vertex" | "fal" | "modelark",
      "api":     "gemini" | "imagen" | "fal" | "modelark",
      "region":  "<vertex region or empty string>",
      "tier":    "cheap" | "premium"   (optional — used by `nazca models` listing only)
    }
  },
  "video": {
    "<shorthand>": {
      "id":      "<raw provider model id>",
      "backend": "vertex" | "fal" | "modelark",
      "tier":    "cheap" | "premium"   (optional)
    }
  }
}
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_APP = "nazca"


def models_path() -> Path:
    """Return the resolved path to the user override file (may not exist)."""
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / _APP / "models.json"


def load_overrides() -> dict:
    """Parse the override file if present; return empty dict otherwise."""
    p = models_path()
    if not p.exists():
        return {}
    try:
        with p.open() as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def image_override(shorthand: str) -> dict | None:
    """Return the image override entry for *shorthand*, or None."""
    ov = load_overrides()
    return ov.get("image", {}).get(shorthand)


def video_override(shorthand: str) -> dict | None:
    """Return the video override entry for *shorthand*, or None."""
    ov = load_overrides()
    return ov.get("video", {}).get(shorthand)


def all_overrides() -> dict:
    """Return the full override dict (``{"image": {...}, "video": {...}}``)."""
    return load_overrides()
