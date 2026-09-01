"""Capability readiness and activation gates.

The Capability Ledger records powers Hermus has discovered or lacks. This module
tracks readiness for those powers and enforces a final activation gate: a
capability may be documented, proposed, implemented or configured, but it is not
usable until an approved activation request exists.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .contracts import CommandStatus, EventEnvelope, EventType

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = ROOT / "data" / "capabilities" / "registry.json"

READINESS_STATES = {"missing", "proposed", "planned", "implemented", "configured", "disabled", "ready", "active", "frozen"}
ACTIVE_STATES = {"active"}


@dataclass
class CapabilityRecord:
    id: str
    name: str
    category: str = "generic"
    status: str = "missing"
    source: str = "manual"
    proposal_path: str = ""
    planning_command: str = ""
    activation_request_id: str = ""
    activation_grant_id: str = ""
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def create(cls, name: str, *, category: str = "generic", status: str = "missing", source: str = "manual", notes: str = "") -> "CapabilityRecord":
        return cls(
            id=f"cap_{uuid.uuid4().hex[:12]}",
            name=_clean(name),
            category=_clean(category or "generic"),
            status=_state(status),
            source=_clean(source or "manual"),
            notes=_clean(notes),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityRecord":
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            category=str(data.get("category") or "generic"),
            status=_state(data.get("status") or "missing"),
            source=str(data.get("source") or "manual"),
            proposal_path=str(data.get("proposal_path") or ""),
            planning_command=str(data.get("planning_command") or ""),
            activation_request_id=str(data.get("activation_request_id") or ""),
            activation_grant_id=str(data.get("activation_grant_id") or ""),
            notes=str(data.get("notes") or ""),
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=str(data.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapabilityRegistry:
    def __init__(self, path: Path = DEFAULT_REGISTRY_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def list(self, include_active: bool = True) -> list[dict[str, Any]]:
        records = self._read()
        if not include_active:
            records = [r for r in records if r.status not in ACTIVE_STATES]
        return [r.to_dict() for r in records]

    def get(self, name_or_id: str) -> Optional[dict[str, Any]]:
        rec = self._find(name_or_id)
        return rec.to_dict() if rec else None

    def register(self, name: str, *, category: str = "generic", status: str = "missing", source: str = "manual", notes: str = "") -> dict[str, Any]:
        if not _clean(name):
            return {"success": False, "error": "capability name required"}
        records = self._read()
        now = datetime.now(timezone.utc).isoformat()
        for rec in records:
            if _key(rec.name) == _key(name):
                rec.category = _clean(category or rec.category)
                rec.status = _state(status or rec.status)
                rec.source = _clean(source or rec.source)
                rec.notes = _clean(notes or rec.notes)
                rec.updated_at = now
                self._write(records)
                self._publish("capability.registry.updated", rec.to_dict())
                return {"success": True, "record": rec.to_dict(), "deduped": True}
        rec = CapabilityRecord.create(name, category=category, status=status, source=source, notes=notes)
        records.append(rec)
        self._write(records)
        self._publish("capability.registry.registered", rec.to_dict())
        return {"success": True, "record": rec.to_dict(), "deduped": False}

    def setup_plan(self, name: str, *, write_proposal: bool = True) -> dict[str, Any]:
        """Create a safe setup/proposal record for a missing capability."""
        registered = self.register(name, status="proposed", source="setup_plan")
        rec = CapabilityRecord.from_dict(registered["record"])
        proposal_result = None
        try:
            from .capability_ledger import get_capability_ledger

            proposal_result = get_capability_ledger().propose(name, write=write_proposal)
            proposal = proposal_result.get("proposal") or {}
            rec.category = proposal.get("category") or rec.category
            rec.proposal_path = proposal_result.get("path") or rec.proposal_path
            rec.planning_command = f"hermus mission start 'Implement capability: {name}' --allow-planning-blocked"
            rec.status = "proposed"
            rec.updated_at = datetime.now(timezone.utc).isoformat()
            self._replace(rec)
        except Exception as exc:  # noqa: BLE001
            proposal_result = {"success": False, "error": str(exc)}
        payload = {"success": bool(proposal_result and proposal_result.get("success", True)), "record": rec.to_dict(), "proposal": proposal_result}
        self._publish("capability.setup.proposed", payload)
        return payload

    def request_activation(self, name_or_id: str, *, reason: str = "") -> dict[str, Any]:
        """Create a pending approval request for activation. No power is granted."""
        rec = self._find(name_or_id)
        if rec is None:
            return {"success": False, "error": "capability not found", "capability": name_or_id}
        if rec.status in {"missing", "proposed", "planned"}:
            return {"success": False, "error": f"capability is {rec.status}; implement/configure before activation", "record": rec.to_dict()}
        try:
            from .permissions import permission_manager

            store = getattr(permission_manager, "approvals", None)
            if store is None:
                return {"success": False, "error": "approval store unavailable", "record": rec.to_dict()}
            safety = {"zone": "yellow", "red_lines": [11], "reasons": ["capability activation requires explicit approval"], "suggested_decision": "ask"}
            result = store.create_request("capability_activate", {"capability": rec.name, "purpose": "activate capability", "reason": reason}, safety)
            if result.get("success"):
                req = result.get("request") or {}
                rec.activation_request_id = req.get("id", "")
                rec.status = "ready"
                rec.updated_at = datetime.now(timezone.utc).isoformat()
                self._replace(rec)
                self._publish("capability.activation.requested", {"record": rec.to_dict(), "request": req})
            return {"success": bool(result.get("success")), "record": rec.to_dict(), "request": result.get("request"), "deduped": result.get("deduped")}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc), "record": rec.to_dict()}

    def activate(self, name_or_id: str, *, approval_id: str) -> dict[str, Any]:
        """Mark a capability active only after its activation request is approved."""
        rec = self._find(name_or_id)
        if rec is None:
            return {"success": False, "error": "capability not found", "capability": name_or_id}
        if not approval_id:
            return {"success": False, "error": "approved activation request id required", "record": rec.to_dict()}
        try:
            from .permissions import permission_manager

            store = getattr(permission_manager, "approvals", None)
            req = store.get_request(approval_id) if store is not None else None
            if req is None or req.status != "approved":
                return {"success": False, "error": "activation request is not approved", "record": rec.to_dict()}
            if req.tool != "capability_activate":
                return {"success": False, "error": "approval request is not for capability activation", "record": rec.to_dict()}
            if _key(req.args_redacted.get("capability")) != _key(rec.name):
                return {"success": False, "error": "approval request capability mismatch", "record": rec.to_dict()}
            rec.status = "active"
            rec.activation_request_id = approval_id
            rec.activation_grant_id = approval_id
            rec.updated_at = datetime.now(timezone.utc).isoformat()
            self._replace(rec)
            self._publish("capability.activated", rec.to_dict())
            return {"success": True, "record": rec.to_dict()}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc), "record": rec.to_dict()}

    def freeze(self, name_or_id: str, *, reason: str = "") -> dict[str, Any]:
        rec = self._find(name_or_id)
        if rec is None:
            return {"success": False, "error": "capability not found", "capability": name_or_id}
        rec.status = "frozen"
        rec.notes = _clean(reason or rec.notes)
        rec.updated_at = datetime.now(timezone.utc).isoformat()
        self._replace(rec)
        self._publish("capability.frozen", rec.to_dict())
        return {"success": True, "record": rec.to_dict()}

    def _read(self) -> list[CapabilityRecord]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("capabilities", [])
        return [CapabilityRecord.from_dict(item) for item in data if isinstance(item, dict)]

    def _write(self, records: list[CapabilityRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"capabilities": [r.to_dict() for r in records]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def _replace(self, record: CapabilityRecord) -> None:
        records = self._read()
        for idx, rec in enumerate(records):
            if rec.id == record.id:
                records[idx] = record
                self._write(records)
                return
        records.append(record)
        self._write(records)

    def _find(self, name_or_id: str) -> Optional[CapabilityRecord]:
        key = _key(name_or_id)
        for rec in self._read():
            if rec.id == name_or_id or _key(rec.name) == key:
                return rec
        return None

    def _publish(self, command: str, data: dict[str, Any]) -> None:
        try:
            from .events import get_bus

            get_bus().publish(EventEnvelope(
                type=EventType.STATE_CHANGED.value,
                command=command,
                target=data.get("id") or data.get("record", {}).get("id"),
                args_redacted=data,
                status=CommandStatus.SUCCEEDED.value,
            ))
        except Exception:
            pass


def get_capability_registry(path: Optional[Path] = None) -> CapabilityRegistry:
    return CapabilityRegistry(path or DEFAULT_REGISTRY_PATH)


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean(value: object) -> str:
    return str(value or "").replace("\n", " ").replace("|", "/").strip()[:300]


def _state(value: object) -> str:
    state = str(value or "missing").lower().strip()
    return state if state in READINESS_STATES else "missing"


__all__ = ["CapabilityRecord", "CapabilityRegistry", "get_capability_registry"]
