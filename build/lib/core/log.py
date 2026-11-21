"""Central structured logging for Hermus.

All library code (``core/*``, ``gateway/*``, ``tools/*``, ...) logs through
:func:`get_logger` instead of ``print`` so that:

* log volume is controllable at runtime (``HERMUS_LOG_LEVEL``),
* machine consumers can request JSON lines (``HERMUS_LOG_FORMAT=json``),
* request correlation works via :func:`bind_request_id` (the gateway binds the
  run/job id; it is rendered on every record while bound).

User-facing CLI output (``hermus.py``, ``tui/*``, ``bootstrap.py``, report
renderers like ``print_diagnostics``) intentionally keeps ``print`` — that is
program output, not diagnostics.

This module is stdlib-only and must never import from ``core`` so every other
module can import it without creating import cycles.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

#: Correlation id for the current request/run/job. Empty when unbound.
request_id: ContextVar[str] = ContextVar("hermus_request_id", default="")

_configured = False


class _ContextFilter(logging.Filter):
    """Attach the bound request id (if any) to every record handled."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id.get()
        record.request_id = rid
        record.request_id_suffix = f" req={rid}" if rid else ""
        return True


class _JsonFormatter(logging.Formatter):
    """Single-line JSON records for machine consumers."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", "")
        if rid:
            payload["request_id"] = rid
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _level_from_env() -> int:
    raw = os.getenv("HERMUS_LOG_LEVEL", "INFO").strip().upper()
    return logging._nameToLevel.get(raw, logging.INFO)


def _format_from_env() -> str:
    return os.getenv("HERMUS_LOG_FORMAT", "text").strip().lower()


def setup_logging(level: int | str | None = None, fmt: str | None = None, stream: Any = None) -> None:
    """Configure the ``hermus`` logger hierarchy. Idempotent.

    Args:
        level: Log level (name or number). Defaults to ``HERMUS_LOG_LEVEL``.
        fmt: ``"text"`` or ``"json"``. Defaults to ``HERMUS_LOG_FORMAT``.
        stream: Where records go. Defaults to ``sys.stderr``.
    """
    global _configured
    if isinstance(level, str):
        resolved_level = logging._nameToLevel.get(level.strip().upper(), logging.INFO)
    else:
        resolved_level = _level_from_env() if level is None else level
    resolved_fmt = _format_from_env() if fmt is None else fmt.strip().lower()

    handler = logging.StreamHandler(stream or sys.stderr)
    # The filter lives on the handler (not the logger) so records from child
    # loggers that propagate up still get the request id attached.
    handler.addFilter(_ContextFilter())
    if resolved_fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s]%(request_id_suffix)s %(message)s"))

    root = logging.getLogger("hermus")
    root.handlers = []
    root.addHandler(handler)
    root.setLevel(resolved_level)
    root.propagate = False

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``hermus`` hierarchy.

    The first call lazily applies :func:`setup_logging` with environment
    defaults so scripts and tests get sane output without explicit setup;
    entry points (CLI, gateway) should still call :func:`setup_logging`
    explicitly at startup.
    """
    if not _configured:
        setup_logging()
    if name == "hermus" or name.startswith("hermus."):
        return logging.getLogger(name)
    return logging.getLogger(f"hermus.{name}")


@contextmanager
def bind_request_id(rid: str) -> Iterator[None]:
    """Bind ``rid`` as the request id for the current context."""
    token = request_id.set(rid)
    try:
        yield
    finally:
        request_id.reset(token)


__all__ = ["bind_request_id", "get_logger", "request_id", "setup_logging"]
