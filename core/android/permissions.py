"""Android consent + allowed-ops control (§19).

No covert surveillance: every Android control operation requires (1) explicit
user consent for that op class, and (2) the op to be within the configured
allowed-ops allowlist. Consent is persisted so a restart does not silently
re-authorize or lose the user's choice; a missing/unset consent is treated as
denied (``android_control_unavailable``), never granted-by-default.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

#: Op classes that require consent. A broader "meta" grant can cover all of them.
OP_CLASSES = ("screen_capture", "ui_control", "launch_app", "device_info", "notification")
_ALLOWED_OPS = {"connect", "get_screen", "get_ui_tree", "tap", "type", "back",
                "launch_app", "observe", "current_app"}


class PermissionDenied(RuntimeError):
    """Raised when an op is not consented to / not allowlisted."""

    def __init__(self, reason: str, *, op: str = "", category: str = "permission"):
        super().__init__(reason)
        self.reason = reason
        self.op = op
        self.category = category


class AndroidPermissionManager:
    """Single writer for Android consent + allowed-ops; persisted to disk."""

    def __init__(self, path: Optional[str] = None):
        default_dir = os.environ.get("HERMUS_DATA_DIR", tempfile.gettempdir())
        self.path = path or str(Path(default_dir) / "hermus_android_permissions.json")
        self._lock = __import__("threading").RLock()
        self._consent: dict[str, bool] = {}
        # The allowlist is a set of *op names* (connect/get_screen/...). Default =
        # every op is allowlisted, but each op still needs the user's *class* consent.
        self._allowed_ops: set[str] = set(_ALLOWED_OPS)
        self._meta_granted: bool = False
        self._load()

    def _load(self) -> None:
        p = Path(self.path)
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self._consent = dict(data.get("consent", {}))
            stored = set(data.get("allowed_ops", _ALLOWED_OPS))
            # Never accept a persisted allowlist containing unknown ops.
            self._allowed_ops = stored & set(_ALLOWED_OPS) or set(_ALLOWED_OPS)
            self._meta_granted = bool(data.get("meta_granted", False))
        except Exception:
            # A corrupt consent file must not silently grant; fall back to denied.
            self._consent = {}
            self._allowed_ops = set()
            self._meta_granted = False

    def _save(self) -> None:
        with self._lock:
            p = Path(self.path)
            p.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "consent": self._consent,
                "allowed_ops": sorted(self._allowed_ops),
                "meta_granted": self._meta_granted,
            }
            # atomic-ish write
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, p)
            os.chmod(p, 0o600)

    # -- consent ------------------------------------------------------------
    def grant(self, op_class: str) -> None:
        if op_class not in OP_CLASSES:
            raise ValueError(f"unknown op class {op_class} (choose {OP_CLASSES})")
        self._consent[op_class] = True
        self._save()

    def revoke(self, op_class: str) -> None:
        self._consent[op_class] = False
        self._save()

    def grant_meta(self, v: bool = True) -> None:
        self._meta_granted = bool(v)
        if v:
            for op in OP_CLASSES:
                self._consent[op] = True
        self._save()

    def is_consented(self, op_class: str) -> bool:
        return bool(self._meta_granted or self._consent.get(op_class, False))

    # -- allowed ops (configurable allowlist) --------------------------------
    def set_allowed_ops(self, ops: list[str]) -> None:
        for o in ops:
            if o not in _ALLOWED_OPS:
                raise ValueError(f"unknown op {o}")
        self._allowed_ops = set(ops)
        self._save()

    def allowed_ops(self) -> list[str]:
        return sorted(self._allowed_ops)

    def is_allowed(self, op: str) -> bool:
        return op in self._allowed_ops

    # -- op -> class --------------------------------------------------------
    @staticmethod
    def op_class(op: str) -> Optional[str]:
        if op in ("get_screen", "observe"):
            return "screen_capture"
        if op == "current_app":
            return "device_info"
        if op in ("get_ui_tree", "tap", "type", "back"):
            return "ui_control"
        if op == "launch_app":
            return "launch_app"
        if op == "connect":
            return "device_info"
        if op in ("notifications", "list_notifications", "click_notification"):
            return "notification"
        return None

    def require_access(self, op: str) -> str:
        """Authorize ``op``; returns the op class or raises PermissionDenied."""
        cls = self.op_class(op)
        if cls is None or not self.is_allowed(op):
            raise PermissionDenied(
                f"op '{op}' is not in the allowed-ops allowlist "
                f"({sorted(self._allowed_ops)})",
                op=op,
            )
        if not self.is_consented(cls):
            raise PermissionDenied(
                f"user has not granted '{cls}' consent; call android_permission_grant "
                f"(\"{cls}\") explicitly — no silent or covert access",
                op=op, category="consent",
            )
        return cls


#: process-wide canonical instance (single consent authority)
_permission_manager: Optional[AndroidPermissionManager] = None


def get_permission_manager() -> AndroidPermissionManager:
    global _permission_manager
    if _permission_manager is None:
        _permission_manager = AndroidPermissionManager()
    return _permission_manager
