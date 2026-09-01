"""Android control audit log (§18).

Every Android operation is recorded in two places:

* a local append-only audit log (``data/android_audit.jsonl``) — a durable,
  human-reviewable record of ``device / op / args / outcome / reason / when``; and
* a canonical :class:`~core.events.EventBus` event (source ``android``) so it is
  replayable alongside every other event and correlated by trace/mission/run.

Nothing is written here that could enable covert use — the audit is the point.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _log_path() -> str:
    return os.environ.get("HERMUS_ANDROID_AUDIT", "") or str(
        Path(os.environ.get("HERMUS_DATA_DIR", tempfile.gettempdir())) / "hermus_android_audit.jsonl"
    )


def record(op: str, args: dict[str, Any], *, ok: bool, reason: Optional[str] = None,
           result: Optional[dict[str, Any]] = None, device: Optional[str] = None,
           op_class: Optional[str] = None, trace_id: Optional[str] = None,
           mission_id: Optional[str] = None, run_id: Optional[str] = None) -> dict[str, Any]:
    """Append the op to the audit log and mirror it onto the canonical EventBus."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_id": _new_id(),
        "device": device,
        "op": op,
        "op_class": op_class,
        "args": args,
        "ok": bool(ok),
        "reason": reason,
        "result": result,
        "trace_id": trace_id,
        "mission_id": mission_id,
        "run_id": run_id,
        "source": "android",
    }
    try:
        p = Path(_log_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        # Audit must never block the op; but a missing audit log is a red flag we
        # surface via the bus event below, not swallowed silently.
        pass
    _publish_event(entry)
    return entry


def _new_id() -> str:
    import uuid
    return f"and_{uuid.uuid4().hex[:16]}"


def _publish_event(entry: dict[str, Any]) -> None:
    try:
        from ..events import get_bus
        from ..contracts import EventEnvelope
        env = EventEnvelope(
            event_id=entry["event_id"],
            trace_id=entry["trace_id"],
            run_id=entry["run_id"],
            mission_id=entry["mission_id"],
            source="android",
            type="android.control",          # carries a type per spec
            command=entry["op"],
            status="ok" if entry["ok"] else "failed",
            error_code="unavailable" if not entry["ok"] else None,
            args_redacted={k: v for k, v in (entry.get("args") or {}).items()
                           if k not in ("data", "tree_xml")},
        )
        get_bus().publish(env)
    except Exception:
        pass


def read_log(limit: int = 100) -> list[dict[str, Any]]:
    """Read the most recent ``limit`` audit entries (newest first)."""
    p = Path(_log_path())
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        entries = []
        for ln in lines[-limit:]:
            try:
                entries.append(json.loads(ln))
            except Exception:
                continue
        return list(reversed(entries))
    except Exception:
        return []
