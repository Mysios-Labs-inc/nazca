"""Local credential config layer — stdlib only, zero new deps.

Config file: $XDG_CONFIG_HOME/nazca/config.ini
            (or ~/.config/nazca/config.ini when XDG_CONFIG_HOME is unset)

Single [default] section with keys: fal_key, ark_api_key
Permissions: directory 0o700, file 0o600.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

_SECTION = "default"
_APP = "nazca"


def config_path() -> Path:
    """Return the resolved path to the config file (file may not exist yet)."""
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / _APP / "config.ini"


def read_config() -> configparser.ConfigParser:
    """Read and return the config; returns an empty parser if the file is missing."""
    cp = configparser.ConfigParser()
    path = config_path()
    if path.exists():
        cp.read(path)
    return cp


def get_value(key: str) -> str | None:
    """Return the value of *key* from [default], or None if missing."""
    cp = read_config()
    return cp.get(_SECTION, key, fallback=None) or None


def set_value(key: str, val: str) -> None:
    """Persist *key*=*val* in [default].

    Creates the config directory (mode 0o700) if needed and chmods the file
    to 0o600 after writing.
    """
    path = config_path()
    # Ensure directory exists with tight permissions.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    cp = read_config()
    if _SECTION not in cp:
        cp[_SECTION] = {}
    cp[_SECTION][key] = val

    with path.open("w") as fh:
        cp.write(fh)

    # Enforce 0o600 regardless of umask.
    path.chmod(0o600)


def mask_value(val: str) -> str:
    """Return a masked representation: first 2 chars + … + last 4 chars, or ***."""
    if len(val) > 8:
        return f"{val[:2]}...{val[-4:]}"
    return "***"


#: Maps a config key → its environment-variable override name.
_ENV_MAP: dict[str, str] = {
    "fal_key": "FAL_KEY",
    "ark_api_key": "ARK_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "atlas_api_key": "ATLAS_API_KEY",
    "vertex_project": "VERTEX_PROJECT",
    "vertex_location": "VERTEX_LOCATION",
}

#: Keys that hold secrets (masked in `config get`/`list`). Project/region are not.
SECRET_KEYS: tuple[str, ...] = ("fal_key", "ark_api_key", "openai_api_key", "atlas_api_key")


def _key_source(key: str) -> tuple[str | None, str]:
    """Return (raw_value, source) where source is 'env', 'file', or 'unset'."""
    env_name = _ENV_MAP.get(key)
    if env_name:
        env_val = os.getenv(env_name)
        if env_val:
            return env_val, "env"
    file_val = get_value(key)
    if file_val:
        return file_val, "file"
    return None, "unset"


def display_value(key: str, val: str) -> str:
    """Mask secret keys for display; show non-secret config (project/region) as-is."""
    return mask_value(val) if key in SECRET_KEYS else val


#: Known config keys (used by `config list` and the login menu).
KNOWN_KEYS: tuple[str, ...] = (
    "fal_key",
    "ark_api_key",
    "openai_api_key",
    "atlas_api_key",
    "vertex_project",
    "vertex_location",
)
