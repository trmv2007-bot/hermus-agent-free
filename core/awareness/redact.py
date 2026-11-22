"""Credential-shaped string redaction for anything the awareness layer shows.

The pattern set mirrors ``core/presence.py`` (which redacts continuity notes);
it is duplicated here rather than imported so a desktop read never depends on
presence state being importable, and so both layers can be read in one file
each. Keep the two in step: a new credential shape belongs in both.
"""

from __future__ import annotations

import re
from typing import Any

SECRET_PATTERNS = (
    (re.compile(r"(?i)(api[_ -]?key|access[_ -]?token|password|secret|token)\s*[:=]\s*[^\s,;]+"), r"\1=[redacted]"),
    (re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_-]{12,})\b"), "[redacted-token]"),
)

# Window titles are single-line by nature; collapse any whitespace anyway so a
# pasted newline cannot forge an extra line in the prompt block.
_WHITESPACE = re.compile(r"\s+")


def redact_text(value: Any, limit: int = 300) -> str:
    """Bound and sanitize one free-text fragment (a window title, an app name)."""
    text = _WHITESPACE.sub(" ", str(value or "")).strip()
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit] + ("…" if len(text) > limit else "")


__all__ = ["SECRET_PATTERNS", "redact_text"]
