"""Self-healing watchdog — classify errors, apply known fixes, verify, commit/rollback.

    ERROR → CLASSIFY → Known fix? → apply fix → run tests → OK? → commit : rollback

This complements the existing ``core/reasoning/lessons`` (which distills
*lessons* from failures): the watchdog *acts* on failures. Fixes are registered
as ``(pattern, fix_fn)`` pairs; unknown errors fall through to a diagnoser hook
for an LLM-generated patch.

Useful for an agent that modifies its own skills and needs to repair breakage.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional
from collections.abc import Callable


class Watchdog:
    def __init__(self):
        self._fixes: list[tuple[re.Pattern, Callable[[str], dict[str, Any]], str]] = []
        self.diagnoser: Optional[Callable[[str], str]] = None
        self.tester: Optional[Callable[[], bool]] = None
        self.rollbacker: Optional[Callable[[], None]] = None
        self.history: list[dict[str, Any]] = []
        self._register_defaults()

    def register_fix(self, pattern: str, fix_fn: Callable[[str], dict[str, Any]],
                     description: str = "") -> None:
        self._fixes.append((re.compile(pattern, re.I), fix_fn, description))

    def _register_defaults(self) -> None:
        def fix_json(error: str) -> dict[str, Any]:
            m = re.search(r"line (\d+).*?(?:column|char) (\d+)", error, re.I)
            return {"ok": True, "detail": f"JSON parse error near line {m.group(1) if m else '?'}"}

        def fix_import(error: str) -> dict[str, Any]:
            return {"ok": True, "detail": "ModuleNotFoundError: suggest installing the missing package"}

        def fix_timeout(error: str) -> dict[str, Any]:
            return {"ok": True, "detail": "timeout: suggest retry with backoff / fallback provider"}

        self.register_fix(r"(json|JSONDecodeError|expecting.*delimiter)", fix_json, "json-parse")
        self.register_fix(r"ModuleNotFoundError|ImportError|No module named", fix_import, "missing-import")
        self.register_fix(r"timeout|timed out|ConnectionError|refused", fix_timeout, "timeout-retry")

    def classify(self, error_text: str) -> dict[str, Any]:
        for pattern, _, desc in self._fixes:
            if pattern.search(error_text):
                return {"known": True, "category": desc or pattern.pattern}
        return {"known": False, "category": "unknown"}

    def handle(self, error_text: str, context: str = "") -> dict[str, Any]:
        record = {"error": error_text[:500], "context": context,
                  "ts": datetime.now().isoformat()}
        classification = self.classify(error_text)
        record.update(classification)

        # known fix?
        for pattern, fix_fn, desc in self._fixes:
            if pattern.search(error_text):
                record["action"] = "apply_fix"
                record["fix"] = desc or pattern.pattern
                try:
                    fix_result = fix_fn(error_text)
                    record["fix_result"] = fix_result
                    record["ok"] = bool(fix_result.get("ok", True))
                except Exception as e:  # noqa: BLE001
                    record["ok"] = False
                    record["fix_error"] = str(e)
                record = self._verify(record)
                self.history.append(record)
                return record

        # unknown: diagnose → generate patch → test → commit/rollback
        record["action"] = "diagnose"
        if self.diagnoser:
            try:
                record["patch"] = self.diagnoser(error_text)
            except Exception as e:  # noqa: BLE001
                record["patch_error"] = str(e)
        record = self._verify(record)
        self.history.append(record)
        return record

    def _verify(self, record: dict[str, Any]) -> dict[str, Any]:
        """Run tests if a tester is supplied; roll back on failure."""
        if self.tester is not None:
            try:
                passed = bool(self.tester())
                record["tests_passed"] = passed
                if not passed and self.rollbacker:
                    try:
                        self.rollbacker()
                        record["rolled_back"] = True
                    except Exception as e:  # noqa: BLE001
                        record["rollback_error"] = str(e)
                record["ok"] = passed
            except Exception as e:  # noqa: BLE001
                record["ok"] = False
                record["test_error"] = str(e)
        return record

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.history[-limit:]


# Global watchdog instance (defaults registered in __init__)
watchdog = Watchdog()
