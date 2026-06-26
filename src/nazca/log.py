"""Centralized logging for nazca.

DIAGNOSTIC CHANNEL BOUNDARY
===========================
Logging here is a *diagnostic* channel and it goes to **stderr only**.

  - It MUST NEVER write to stdout.
  - It MUST NEVER replace a ``click.echo(...)`` call. ``click.echo`` is the
    program's *user-facing output* (results, prompts, machine-readable data
    that callers may pipe). Logging is for the developer/operator watching
    the run — progress, warnings, debug traces.
  - Keep the two separate: if a human or a downstream pipe is meant to read
    it, use ``click.echo`` (stdout); if it's a diagnostic, use a logger
    (stderr).

LIBRARY-SAFE BY DEFAULT
=======================
At import time we attach a :class:`logging.NullHandler` to the ``nazca``
logger and configure nothing else. So importing nazca as a library is
SILENT and never touches the root logger or handler set. Only entrypoints
(the CLI and the MCP server) call :func:`configure` to actually emit logs.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

__all__ = [
    "get_logger",
    "configure",
    "level_from_verbosity",
    "redact",
]

ROOT_NAME = "nazca"

# Library-safe default: attach a NullHandler so the "nazca" logger never emits
# anything (and Python never installs a last-resort handler) until an
# entrypoint explicitly calls configure(). This runs once, at import.
logging.getLogger(ROOT_NAME).addHandler(logging.NullHandler())


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger under the ``nazca`` namespace.

    ``get_logger()`` -> the root ``nazca`` logger.
    ``get_logger("image")`` -> ``nazca.image``.
    """
    if name is None:
        return logging.getLogger(ROOT_NAME)
    return logging.getLogger(f"{ROOT_NAME}.{name}")


class _StderrHandler(logging.StreamHandler):
    """A StreamHandler that resolves ``sys.stderr`` lazily, at emit time.

    Mirrors the stdlib ``logging._StderrHandler`` used by ``logging.lastResort``.
    Binding a normal ``StreamHandler(sys.stderr)`` snapshots the stream object —
    fragile under harnesses that swap and then close ``sys.stderr`` per call
    (e.g. click's ``CliRunner``), which yields "I/O operation on closed file" on
    a later record. Resolving the stream dynamically always writes to the current,
    open ``sys.stderr`` and never to stdout.
    """

    def __init__(self, level: int | str = logging.NOTSET) -> None:
        logging.Handler.__init__(self, level)

    @property
    def stream(self) -> TextIO:  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, value: TextIO) -> None:  # pragma: no cover - dynamic resolve
        # Intentionally ignore: the stream is always the live sys.stderr.
        pass


def configure(level: int | str = "WARNING", *, stream: TextIO | None = None) -> None:
    """Attach a single stderr handler to the ``nazca`` logger.

    Called ONLY by entrypoints (CLI / MCP), never at import time. Idempotent:
    a second call updates the level but does not add a second handler.

    Args:
        level: Logging level as an int (e.g. ``logging.DEBUG``) or name
            (e.g. ``"DEBUG"``).
        stream: Optional explicit stream (used by tests). When ``None`` (the
            default and the entrypoint path), diagnostics go to the live
            ``sys.stderr``, resolved at emit time — never to stdout.
    """
    logger = logging.getLogger(ROOT_NAME)
    logger.setLevel(level)
    logger.propagate = False

    # Idempotency: reuse an existing real (non-Null) handler if one is attached.
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.NullHandler
        ):
            handler.setLevel(level)
            if stream is not None:
                handler.setStream(stream)
            return

    handler: logging.StreamHandler = (
        logging.StreamHandler(stream) if stream is not None else _StderrHandler()
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)


def level_from_verbosity(verbose: int) -> int:
    """Map a ``-v`` count to a logging level: 0->WARNING, 1->INFO, >=2->DEBUG."""
    if verbose <= 0:
        return logging.WARNING
    if verbose == 1:
        return logging.INFO
    return logging.DEBUG


def redact(value: str | None) -> str:
    """Mask secrets and base64 data-URIs for safe logging.

    - ``None``/empty -> returned as a short placeholder, never raises.
    - ``data:`` URIs -> summarized as ``data:<mime>;base64,<NN bytes>``.
    - Long opaque tokens -> middle masked, e.g. ``sk...d999``.
    - Short plain strings -> returned mostly intact.
    """
    if not value:
        return ""

    text = str(value)

    # data: URI — summarize instead of dumping the whole base64 blob.
    if text.startswith("data:"):
        header, _, payload = text[len("data:") :].partition(",")
        mime, _, encoding = header.partition(";")
        mime = mime or "application/octet-stream"
        suffix = f";{encoding}" if encoding else ""
        return f"data:{mime}{suffix},<{len(payload)} bytes>"

    # Long opaque string (likely an API key/token) — mask the middle.
    if len(text) > 12 and " " not in text:
        return f"{text[:2]}...{text[-4:]}"

    return text
