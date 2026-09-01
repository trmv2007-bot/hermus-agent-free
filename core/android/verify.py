"""Android action verification (§8).

Every meaningful action should be verified: capture a before-observation, execute,
capture an after-observation, then confirm the expected state change. If the check
fails the caller retries / replans — the helper never blindly reports success.
"""
from __future__ import annotations

from typing import Any, Callable, Optional


class AndroidVerifier:
    """Before -> action -> after -> verify for Android ops."""

    def __init__(self, tool: Any):
        self._tool = tool

    def capture(self) -> dict[str, Any]:
        obs = self._tool.get_ui_tree()
        return obs

    def run_verified(self, op: str, args: dict[str, Any], *,
                     expect: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
                     before: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        before = before or self.capture()
        result = self._tool.run(op, args)
        after = self.capture()
        screen_changed = (self._screen_hash(before) != self._screen_hash(after))
        expected = bool(expect and expect(before, after))
        ok = bool(result.get("ok")) and (expected if expect is not None else True)
        return {
            "ok": ok,
            "op": op,
            "action_ok": bool(result.get("ok")),
            "screen_changed": screen_changed,
            "expected_state": expected,
            "before": self._public(before),
            "after": self._public(after),
            "result": result,
        }

    @staticmethod
    def _screen_hash(obs: dict[str, Any]) -> str:
        import json
        return hashlib_sha(json.dumps(obs, sort_keys=True, default=str))

    @staticmethod
    def _public(obs: dict[str, Any]) -> dict[str, Any]:
        return {"package": obs.get("package"), "title": obs.get("title"),
                "nodes": obs.get("nodes")}


def hashlib_sha(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:16]


# Common expectation predicates --------------------------------------------------
def app_launched(package: str):
    def expect(before: dict[str, Any], after: dict[str, Any]) -> bool:
        return after.get("package") == package
    return expect


def text_present(text: str):
    def expect(before: dict[str, Any], after: dict[str, Any]) -> bool:
        blob = (after.get("nodes") or [])
        return any((n.get("text") or "") == text for n in blob)
    return expect
