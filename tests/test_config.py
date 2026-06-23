"""Tests for config.py PEP-562 lazy/fresh attribute resolution."""

from __future__ import annotations

import pytest

import nazca.config as config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_real_attr(name: str) -> None:
    """Remove a real module attribute if it exists (cleanup helper)."""
    config.__dict__.pop(name, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fresh_env_read(monkeypatch):
    """VERTEX_PROJECT resolves from env without import-time staleness."""
    # Ensure no real module attr is shadowing __getattr__.
    _clear_real_attr("VERTEX_PROJECT")

    monkeypatch.setenv("VERTEX_PROJECT", "p-from-env")
    assert config.VERTEX_PROJECT == "p-from-env"

    # Cleanup: env var reverted by monkeypatch automatically.
    _clear_real_attr("VERTEX_PROJECT")


def test_default_vertex_location(monkeypatch):
    """VERTEX_LOCATION falls back to 'us-central1' when env + ini are unset."""
    _clear_real_attr("VERTEX_LOCATION")

    # Remove env var if set.
    monkeypatch.delenv("VERTEX_LOCATION", raising=False)
    # Patch get_value so the ini file also returns nothing.
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)

    assert config.VERTEX_LOCATION == "us-central1"

    _clear_real_attr("VERTEX_LOCATION")


def test_runtime_write_shadows(monkeypatch):
    """An explicit assignment (as done by setup.py) shadows __getattr__."""
    _clear_real_attr("VERTEX_PROJECT")
    monkeypatch.delenv("VERTEX_PROJECT", raising=False)
    monkeypatch.setattr("nazca.config.get_value", lambda key: None)

    # Simulate what setup.py does.
    config.VERTEX_PROJECT = "explicit-proj"
    try:
        assert config.VERTEX_PROJECT == "explicit-proj"
    finally:
        # Clean up the real attribute so it falls back to __getattr__.
        _clear_real_attr("VERTEX_PROJECT")

    # After deletion, should fall back to fresh resolution (returns None here).
    assert config.VERTEX_PROJECT is None


def test_monkeypatch_setattr_works(monkeypatch):
    """monkeypatch.setattr / setattr on the module still works (tests rely on this)."""
    _clear_real_attr("VERTEX_PROJECT")

    monkeypatch.setattr(config, "VERTEX_PROJECT", "test-proj")
    assert config.VERTEX_PROJECT == "test-proj"

    # monkeypatch reverts automatically; after that __getattr__ takes over again.


def test_monkeypatch_setattr_none(monkeypatch):
    """monkeypatch.setattr to None is accepted (used in test_modality_routing)."""
    _clear_real_attr("VERTEX_PROJECT")

    monkeypatch.setattr(config, "VERTEX_PROJECT", None)
    assert config.VERTEX_PROJECT is None


def test_poll_ints(monkeypatch):
    """POLL_INTERVAL and POLL_MAX_TRIES are ints with correct defaults."""
    _clear_real_attr("POLL_INTERVAL")
    _clear_real_attr("POLL_MAX_TRIES")

    monkeypatch.delenv("VEO_POLL_INTERVAL", raising=False)
    monkeypatch.delenv("VEO_POLL_MAX_TRIES", raising=False)

    assert isinstance(config.POLL_INTERVAL, int)
    assert config.POLL_INTERVAL == 15

    assert isinstance(config.POLL_MAX_TRIES, int)
    assert config.POLL_MAX_TRIES == 60

    _clear_real_attr("POLL_INTERVAL")
    _clear_real_attr("POLL_MAX_TRIES")


def test_poll_ints_from_env(monkeypatch):
    """POLL_INTERVAL and POLL_MAX_TRIES honour env var overrides."""
    _clear_real_attr("POLL_INTERVAL")
    _clear_real_attr("POLL_MAX_TRIES")

    monkeypatch.setenv("VEO_POLL_INTERVAL", "5")
    monkeypatch.setenv("VEO_POLL_MAX_TRIES", "120")

    assert config.POLL_INTERVAL == 5
    assert config.POLL_MAX_TRIES == 120

    _clear_real_attr("POLL_INTERVAL")
    _clear_real_attr("POLL_MAX_TRIES")


def test_unknown_attr_raises():
    """__getattr__ raises AttributeError for unknown names (not silent None)."""
    with pytest.raises(AttributeError):
        _ = config.TOTALLY_UNKNOWN_NAME_XYZ  # type: ignore[attr-defined]


def test_api_keys_from_env(monkeypatch):
    """FAL_KEY, ARK_API_KEY, OPENAI_API_KEY resolve from environment."""
    for attr, env_name in [
        ("FAL_KEY", "FAL_KEY"),
        ("ARK_API_KEY", "ARK_API_KEY"),
        ("OPENAI_API_KEY", "OPENAI_API_KEY"),
    ]:
        _clear_real_attr(attr)
        monkeypatch.setenv(env_name, f"test-{env_name.lower()}")
        assert getattr(config, attr) == f"test-{env_name.lower()}"
        _clear_real_attr(attr)
