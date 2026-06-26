"""Tests for the centralized logging module (src/nazca/log.py).

Contract under test:
  - library-safe by default (NullHandler, silent on stdout AND stderr)
  - configure() attaches one stderr StreamHandler at the given level
  - configure() is idempotent
  - redact() masks data: URIs and long tokens, leaves short strings intact
  - level_from_verbosity() maps 0/1/2 correctly
"""

from __future__ import annotations

import logging

import pytest

from nazca import log as nazca_log


@pytest.fixture(autouse=True)
def _reset_nazca_logger():
    """Give each test a library-safe baseline and restore the original after.

    The full suite invokes the CLI many times in one process, and each invoke
    calls ``log.configure()`` (correct in production — one handler per process),
    which leaves a stderr handler on the shared ``nazca`` logger. Snapshot-only
    restore would inherit that pollution, so we reset to the clean import-time
    state (NullHandler, default level) BEFORE each test, then restore the
    original handler set afterward.
    """
    logger = logging.getLogger("nazca")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    # Clean baseline: keep only NullHandler(s), drop any leaked stream handler.
    null_handlers = [h for h in logger.handlers if isinstance(h, logging.NullHandler)]
    logger.handlers[:] = null_handlers or [logging.NullHandler()]
    logger.setLevel(logging.WARNING)
    logger.propagate = True
    try:
        yield
    finally:
        logger.handlers[:] = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


def test_default_is_library_safe_null_handler_and_silent(capsys):
    """By default the nazca logger has a NullHandler and emits nothing."""
    logger = logging.getLogger("nazca")
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)

    # No StreamHandler should be attached by mere import.
    stream_handlers = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.NullHandler)
    ]
    assert stream_handlers == []

    nazca_log.get_logger("image").warning("should not appear anywhere")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_configure_sets_debug_level_and_stderr_streamhandler(capsys):
    nazca_log.configure("DEBUG")
    logger = logging.getLogger("nazca")

    assert logger.level == logging.DEBUG
    assert logger.propagate is False

    stream_handlers = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.NullHandler)
    ]
    assert len(stream_handlers) == 1
    # The handler targets stderr, never stdout.
    assert stream_handlers[0].stream is __import__("sys").stderr

    nazca_log.get_logger("video").debug("diagnostic line")
    captured = capsys.readouterr()
    assert captured.out == ""  # never stdout
    assert "diagnostic line" in captured.err


def test_configure_is_idempotent():
    nazca_log.configure("INFO")
    nazca_log.configure("DEBUG")
    logger = logging.getLogger("nazca")

    stream_handlers = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.NullHandler)
    ]
    assert len(stream_handlers) == 1
    # Second call still updated the level.
    assert logger.level == logging.DEBUG


def test_redact_masks_data_uri():
    uri = "data:image/png;base64," + ("A" * 5000)
    out = nazca_log.redact(uri)
    assert out.startswith("data:image/png")
    assert "base64" in out
    assert "5000 bytes" in out
    assert "A" * 100 not in out  # the blob itself is gone


def test_redact_masks_long_token_but_keeps_short_string():
    token = "sk-abcdefghijklmnopqrstuvwxyzd999"
    masked = nazca_log.redact(token)
    assert masked != token
    assert masked.startswith("sk")
    assert masked.endswith("d999")
    assert "abcdefghij" not in masked

    short = "hello"
    assert nazca_log.redact(short) == "hello"

    assert nazca_log.redact(None) == ""
    assert nazca_log.redact("") == ""


def test_level_from_verbosity():
    assert nazca_log.level_from_verbosity(0) == logging.WARNING
    assert nazca_log.level_from_verbosity(1) == logging.INFO
    assert nazca_log.level_from_verbosity(2) == logging.DEBUG
    assert nazca_log.level_from_verbosity(5) == logging.DEBUG
