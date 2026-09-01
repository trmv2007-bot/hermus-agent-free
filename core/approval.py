"""Structured approval/scope grants for yellow red-line actions.

A yellow action is not forbidden; it needs explicit scope. This module stores
small, auditable grants such as:

- allow reading ``~/Downloads`` for malware search for 30 minutes
- allow sending daily Telegram reports
- allow trading only from an isolated agent wallet/account with limits

The permission manager uses these grants to turn matching ASK decisions into
ALLOW decisions. Red-zone actions are never granted here.
"""
from __future__ import annotations

import fnmatch
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class ApprovalRequest:
    id: str
    tool: str
    args_redacted: dict[str, Any]
    safety: dict[str, Any]
    status: str = "pending"
    title: str = "Approval required"
    suggested_resources: list[str] = field(default_factory=list)
    suggested_purpose: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: Optional[str] = None
    resolution: str = ""

    @classmethod
    def create(cls, tool: str, args: dict[str, Any], safety: dict[str, Any]) -> "ApprovalRequest":
        return cls(
            id=f"approval_{uuid.uuid4().hex[:12]}",
            tool=tool,
            args_redacted=_redact_args(args),
            safety=dict(safety or {}),
            title=f"Approve {tool}",
            suggested_resources=_suggest_resources(args),
            suggested_purpose=_suggest_purpose(args, safety),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalRequest":
        return cls(
            id=str(data["id"]),
            tool=str(data.get("tool") or ""),
            args_redacted=dict(data.get("args_redacted") or {}),
            safety=dict(data.get("safety") or {}),
            status=str(data.get("status") or "pending"),
            title=str(data.get("title") or "Approval required"),
            suggested_resources=[str(x) for x in data.get("suggested_resources", [])],
            suggested_purpose=str(data.get("suggested_purpose") or ""),
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
            resolved_at=data.get("resolved_at"),
            resolution=str(data.get("resolution") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalBundle:
    id: str
    title: str
    request_ids: list[str] = field(default_factory=list)
    mission_id: str = ""
    goal: str = ""
    status: str = "pending"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: Optional[str] = None
    resolution: str = ""

    @classmethod
    def create(cls, title: str, request_ids: list[str], *, mission_id: str = "", goal: str = "") -> "ApprovalBundle":
        return cls(
            id=f"bundle_{uuid.uuid4().hex[:12]}",
            title=title or "Approval bundle",
            request_ids=list(request_ids or []),
            mission_id=mission_id or "",
            goal=goal or "",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalBundle":
        return cls(
            id=str(data["id"]),
            title=str(data.get("title") or data["id"]),
            request_ids=[str(x) for x in data.get("request_ids", [])],
            mission_id=str(data.get("mission_id") or ""),
            goal=str(data.get("goal") or ""),
            status=str(data.get("status") or "pending"),
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
            resolved_at=data.get("resolved_at"),
            resolution=str(data.get("resolution") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalGrant:
    id: str
    title: str
    tool: str = "*"
    zone: str = "yellow"
    red_lines: list[int] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    purpose: str = ""
    decision: str = "allow"
    expires_at: Optional[str] = None
    max_uses: Optional[int] = None
    uses: int = 0
    active: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: str = "user"
    notes: str = ""

    @classmethod
    def create(
        cls,
        title: str,
        *,
        tool: str = "*",
        red_lines: Optional[list[int]] = None,
        resources: Optional[list[str]] = None,
        purpose: str = "",
        ttl_minutes: Optional[int] = None,
        max_uses: Optional[int] = None,
        created_by: str = "user",
        notes: str = "",
    ) -> "ApprovalGrant":
        expires_at = None
        if ttl_minutes is not None and int(ttl_minutes) > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=int(ttl_minutes))).isoformat()
        return cls(
            id=f"grant_{uuid.uuid4().hex[:12]}",
            title=title,
            tool=tool or "*",
            red_lines=list(red_lines or []),
            resources=list(resources or []),
            purpose=purpose or "",
            expires_at=expires_at,
            max_uses=max_uses,
            created_by=created_by or "user",
            notes=notes or "",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalGrant":
        return cls(
            id=str(data["id"]),
            title=str(data.get("title") or data["id"]),
            tool=str(data.get("tool") or "*"),
            zone=str(data.get("zone") or "yellow"),
            red_lines=[int(x) for x in data.get("red_lines", [])],
            resources=[str(x) for x in data.get("resources", [])],
            purpose=str(data.get("purpose") or ""),
            decision=str(data.get("decision") or "allow"),
            expires_at=data.get("expires_at"),
            max_uses=data.get("max_uses"),
            uses=int(data.get("uses", 0)),
            active=bool(data.get("active", True)),
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
            created_by=str(data.get("created_by") or "user"),
            notes=str(data.get("notes") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def expired(self, now: Optional[datetime] = None) -> bool:
        if not self.expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            now = now or datetime.now(timezone.utc)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return now >= expiry
        except Exception:
            return True

    def exhausted(self) -> bool:
        return self.max_uses is not None and self.uses >= int(self.max_uses)

    def valid(self) -> bool:
        return self.active and self.decision == "allow" and not self.expired() and not self.exhausted()

    def matches(self, tool_name: str, args: dict[str, Any], safety: dict[str, Any]) -> tuple[bool, str]:
        if not self.valid():
            return False, "grant inactive, expired, exhausted, or non-allow"
        if self.zone and safety.get("zone") and self.zone != safety.get("zone"):
            return False, "zone mismatch"
        if self.tool not in ("*", tool_name) and not fnmatch.fnmatch(tool_name, self.tool):
            return False, "tool mismatch"
        safety_lines = {int(x) for x in safety.get("red_lines", [])}
        if self.red_lines and not safety_lines.issubset(set(self.red_lines)):
            return False, "red-line mismatch"
        if self.resources and not _args_match_resources(args, self.resources):
            return False, "resource scope mismatch"
        if self.purpose:
            purpose_text = _flatten_args(args).lower()
            required = self.purpose.lower()
            if required not in purpose_text and not any(word and word in purpose_text for word in required.replace("_", " ").split()):
                return False, "purpose mismatch"
        return True, "matched"


class ApprovalStore:
    def __init__(self, path: Path, requests_path: Optional[Path] = None, bundles_path: Optional[Path] = None):
        self.path = Path(path)
        self.requests_path = Path(requests_path) if requests_path is not None else self.path.with_name("approval_requests.json")
        self.bundles_path = Path(bundles_path) if bundles_path is not None else self.path.with_name("approval_bundles.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.requests_path.parent.mkdir(parents=True, exist_ok=True)
        self.bundles_path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> list[ApprovalGrant]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("grants", [])
        return [ApprovalGrant.from_dict(item) for item in data if isinstance(item, dict)]

    def _write(self, grants: list[ApprovalGrant]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"grants": [g.to_dict() for g in grants]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def list(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        grants = self._read()
        if not include_inactive:
            grants = [g for g in grants if g.valid()]
        return [g.to_dict() for g in grants]

    def _read_requests(self) -> list[ApprovalRequest]:
        if not self.requests_path.exists():
            return []
        try:
            data = json.loads(self.requests_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("requests", [])
        return [ApprovalRequest.from_dict(item) for item in data if isinstance(item, dict)]

    def _write_requests(self, requests: list[ApprovalRequest]) -> None:
        self.requests_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"requests": [r.to_dict() for r in requests]}
        tmp = self.requests_path.with_suffix(self.requests_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.requests_path)

    def pending(self, include_resolved: bool = False) -> list[dict[str, Any]]:
        requests = self._read_requests()
        if not include_resolved:
            requests = [r for r in requests if r.status == "pending"]
        return [r.to_dict() for r in requests]

    def _read_bundles(self) -> list[ApprovalBundle]:
        if not self.bundles_path.exists():
            return []
        try:
            data = json.loads(self.bundles_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("bundles", [])
        return [ApprovalBundle.from_dict(item) for item in data if isinstance(item, dict)]

    def _write_bundles(self, bundles: list[ApprovalBundle]) -> None:
        self.bundles_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"bundles": [b.to_dict() for b in bundles]}
        tmp = self.bundles_path.with_suffix(self.bundles_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.bundles_path)

    def bundles(self, include_resolved: bool = False) -> list[dict[str, Any]]:
        bundles = self._read_bundles()
        if not include_resolved:
            bundles = [b for b in bundles if b.status == "pending"]
        requests = {r.id: r.to_dict() for r in self._read_requests()}
        out = []
        for bundle in bundles:
            data = bundle.to_dict()
            data["requests"] = [requests[rid] for rid in bundle.request_ids if rid in requests]
            out.append(data)
        return out

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        for req in self._read_requests():
            if req.id == request_id:
                return req
        return None

    def create_request(self, tool_name: str, args: dict[str, Any], safety: dict[str, Any]) -> dict[str, Any]:
        if (safety or {}).get("zone") != "yellow":
            return {"success": False, "error": "approval requests are only created for yellow-zone actions"}
        # Avoid flooding the inbox with exact duplicates while one is pending.
        candidate = ApprovalRequest.create(tool_name, args, safety)
        requests = self._read_requests()
        for existing in requests:
            if existing.status == "pending" and existing.tool == candidate.tool and existing.args_redacted == candidate.args_redacted and existing.safety == candidate.safety:
                return {"success": True, "request": existing.to_dict(), "deduped": True}
        requests.append(candidate)
        self._write_requests(requests)
        self._audit("approval_requested", candidate.to_dict())
        self._publish("permission.approval.requested", candidate.to_dict(), status="pending")
        return {"success": True, "request": candidate.to_dict(), "deduped": False}

    def create_bundle(self, title: str, request_ids: list[str], *, mission_id: str = "", goal: str = "") -> dict[str, Any]:
        request_ids = [str(x) for x in request_ids if x]
        if not request_ids:
            return {"success": False, "error": "request_ids required"}
        existing = self._read_bundles()
        for bundle in existing:
            if bundle.status == "pending" and set(bundle.request_ids) == set(request_ids) and bundle.mission_id == (mission_id or ""):
                return {"success": True, "bundle": bundle.to_dict(), "deduped": True}
        bundle = ApprovalBundle.create(title, request_ids, mission_id=mission_id, goal=goal)
        existing.append(bundle)
        self._write_bundles(existing)
        self._audit("approval_bundle_created", bundle.to_dict())
        self._publish("permission.approval.bundle.created", bundle.to_dict(), status="pending")
        return {"success": True, "bundle": bundle.to_dict(), "deduped": False}

    def resolve_bundle(
        self,
        bundle_id: str,
        decision: str,
        *,
        ttl_minutes: Optional[int] = None,
        max_uses: Optional[int] = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if decision not in {"approve", "deny"}:
            return {"success": False, "error": "decision must be approve or deny", "id": bundle_id}
        bundles = self._read_bundles()
        target = None
        for bundle in bundles:
            if bundle.id == bundle_id:
                target = bundle
                break
        if target is None:
            return {"success": False, "error": "approval bundle not found", "id": bundle_id}
        if target.status != "pending":
            return {"success": False, "error": f"approval bundle already {target.status}", "bundle": target.to_dict()}
        results = []
        for req_id in target.request_ids:
            req = self.get_request(req_id)
            if req is None:
                results.append({"id": req_id, "success": False, "error": "request missing"})
            elif req.status == "pending":
                results.append(self.resolve_request(
                    req_id,
                    decision,
                    ttl_minutes=ttl_minutes,
                    max_uses=max_uses,
                    notes=notes or f"Resolved via bundle {bundle_id}",
                ))
            else:
                results.append({"id": req_id, "success": True, "request": req.to_dict(), "already": req.status})
        target.status = "approved" if decision == "approve" else "denied"
        target.resolved_at = datetime.now(timezone.utc).isoformat()
        target.resolution = notes or decision
        self._write_bundles(bundles)
        payload = {"id": bundle_id, "decision": decision, "bundle": target.to_dict(), "results": results}
        self._audit("approval_bundle_resolved", payload)
        self._publish("permission.approval.bundle.resolved", payload, status=target.status)
        return {"success": all(r.get("success") for r in results), "bundle": target.to_dict(), "results": results}

    def resolve_request(
        self,
        request_id: str,
        decision: str,
        *,
        resources: Optional[list[str]] = None,
        purpose: str = "",
        ttl_minutes: Optional[int] = None,
        max_uses: Optional[int] = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if decision not in {"approve", "deny"}:
            return {"success": False, "error": "decision must be approve or deny", "id": request_id}
        requests = self._read_requests()
        target: Optional[ApprovalRequest] = None
        for req in requests:
            if req.id == request_id:
                target = req
                break
        if target is None:
            return {"success": False, "error": "approval request not found", "id": request_id}
        if target.status != "pending":
            return {"success": False, "error": f"approval request already {target.status}", "request": target.to_dict()}

        target.status = "approved" if decision == "approve" else "denied"
        target.resolved_at = datetime.now(timezone.utc).isoformat()
        target.resolution = notes or decision
        grant_result = None
        if decision == "approve":
            safety_lines = [int(x) for x in target.safety.get("red_lines", [])]
            grant = ApprovalGrant.create(
                title=f"Grant for {target.tool} via {target.id}",
                tool=target.tool,
                red_lines=safety_lines,
                resources=resources if resources is not None else target.suggested_resources,
                purpose=purpose or target.suggested_purpose,
                ttl_minutes=ttl_minutes,
                max_uses=max_uses,
                notes=notes or f"Created from pending approval {target.id}",
            )
            grants = self._read()
            grants.append(grant)
            self._write(grants)
            grant_result = grant.to_dict()
        self._write_requests(requests)
        payload = {"id": request_id, "decision": decision, "request": target.to_dict(), "grant": grant_result}
        self._audit("approval_resolved", payload)
        self._publish("permission.approval.resolved", payload, status=target.status)
        return {"success": True, "request": target.to_dict(), "grant": grant_result}

    def add(self, grant: ApprovalGrant) -> dict[str, Any]:
        grants = self._read()
        grants.append(grant)
        self._write(grants)
        self._audit("grant_added", grant.to_dict())
        self._publish("permission.grant.added", grant.to_dict(), status="active")
        return {"success": True, "grant": grant.to_dict()}

    def create(self, **kwargs: Any) -> dict[str, Any]:
        return self.add(ApprovalGrant.create(**kwargs))

    def revoke(self, grant_id: str) -> dict[str, Any]:
        grants = self._read()
        changed = False
        for grant in grants:
            if grant.id == grant_id:
                grant.active = False
                changed = True
        if changed:
            self._write(grants)
            self._audit("grant_revoked", {"id": grant_id})
            self._publish("permission.grant.revoked", {"id": grant_id}, status="revoked")
        return {"success": changed, "id": grant_id}

    def find_match(self, tool_name: str, args: dict[str, Any], safety: dict[str, Any]) -> Optional[ApprovalGrant]:
        for grant in self._read():
            ok, _ = grant.matches(tool_name, args, safety)
            if ok:
                return grant
        return None

    def allowed(self, tool_name: str, args: dict[str, Any], safety: dict[str, Any], *, consume: bool = False) -> Optional[dict[str, Any]]:
        grants = self._read()
        for grant in grants:
            ok, reason = grant.matches(tool_name, args, safety)
            if not ok:
                continue
            if consume:
                grant.uses += 1
                self._write(grants)
                self._audit("grant_used", {"id": grant.id, "tool": tool_name, "uses": grant.uses, "safety": safety})
            return {"matched": True, "grant": grant.to_dict(), "reason": reason}
        return None

    def _audit(self, action: str, data: dict[str, Any]) -> None:
        try:
            audit_path = self.path.parent / "approval_grants.audit.jsonl"
            record = {"ts": datetime.now(timezone.utc).isoformat(), "action": action, **data}
            with audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
        except Exception:
            pass

    def _publish(self, command: str, data: dict[str, Any], *, status: str) -> None:
        try:
            from .contracts import Actor, CommandSource, EventEnvelope, EventType
            from .events import get_bus

            get_bus().publish(EventEnvelope(
                actor=Actor.SYSTEM.value,
                source=CommandSource.INTERNAL.value,
                type=EventType.PERMISSION_CHECKED.value,
                command=command,
                target=data.get("id") or data.get("tool"),
                args_redacted=_redact_args(data),
                status=status,
            ))
        except Exception:
            pass


def _redact_args(args: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    secret_words = ("secret", "token", "password", "credential", "cookie", "api_key", "apikey", "private_key")
    for key, value in (args or {}).items():
        lowered = str(key).lower()
        if any(word in lowered for word in secret_words):
            redacted[key] = "<redacted>"
        elif isinstance(value, dict):
            redacted[key] = _redact_args(value)
        elif isinstance(value, (list, tuple)):
            redacted[key] = ["<redacted>" if any(word in str(x).lower() for word in secret_words) else x for x in value]
        else:
            text = str(value)
            if any(word in text.lower() for word in ("sk-", "ghp_", "github_pat_", "akia")):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = value
    return redacted


def _suggest_resources(args: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for value in _path_values(args):
        text = str(value)
        for token in ("~/Downloads", "~/Documents", "~/", "/home/", "192.168.", "10.", "172.16."):
            if token in text and token not in out:
                out.append(token)
    return out


def _suggest_purpose(args: dict[str, Any], safety: dict[str, Any]) -> str:
    text = _flatten_args(args).lower()
    for word in ("malware", "incident", "backup", "recovery", "daily_report", "report", "trading", "wallet", "deploy", "message"):
        if word in text:
            return word
    lines = set(int(x) for x in (safety or {}).get("red_lines", []))
    if 3 in lines:
        return "defensive_scan"
    if 4 in lines or 8 in lines:
        return "authorized_security"
    if 6 in lines:
        return "agent_wallet"
    if 9 in lines:
        return "delegated_communication"
    return ""


def _flatten_args(args: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in sorted((args or {}).items()):
        if isinstance(value, dict):
            parts.append(f"{key} " + _flatten_args(value))
        elif isinstance(value, (list, tuple, set)):
            parts.append(f"{key} " + " ".join(str(x) for x in value))
        else:
            parts.append(f"{key} {value}")
    return " ".join(parts)


def _args_match_resources(args: dict[str, Any], resources: list[str]) -> bool:
    text = _flatten_args(args).replace("\\", "/")
    lowered = text.lower()
    for raw in resources:
        pattern = str(raw).replace("\\", "/")
        p_low = pattern.lower()
        if p_low in lowered:
            return True
        # Also try glob matching against path-like argument values.
        for value in _path_values(args):
            value_norm = str(value).replace("\\", "/")
            if fnmatch.fnmatch(value_norm, pattern) or fnmatch.fnmatch(value_norm.lower(), p_low):
                return True
    return False


def _path_values(args: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key, value in (args or {}).items():
        if isinstance(value, dict):
            out.extend(_path_values(value))
        elif isinstance(value, (list, tuple, set)):
            out.extend(str(x) for x in value)
        elif key.lower() in {"path", "file", "folder", "directory", "target", "url", "resource", "command", "query", "scope"}:
            out.append(str(value))
    return out


__all__ = ["ApprovalBundle", "ApprovalGrant", "ApprovalRequest", "ApprovalStore"]
