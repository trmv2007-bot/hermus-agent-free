"""Phase 17 distributed coordination layer.

Provides a durable registry of authorized HERMUS nodes and a conservative
dispatcher that selects a healthy node by declared capability. It coordinates
jobs but never grants permissions: every node remains subject to its local
approval, red-line, sandbox, verification and emergency-stop controls.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import config
from .emergency_stop import get_emergency_stop


@dataclass
class HermusNode:
    id: str
    name: str
    capabilities: list[str] = field(default_factory=list)
    endpoint: str = ""
    status: str = "online"
    last_heartbeat: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    lease_until: float = 0.0
    fencing_token: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DistributedAssignment:
    id: str
    job_id: str
    node_id: str
    capability: str = ""
    status: str = "assigned"
    created_at: float = field(default_factory=time.time)
    reason: str = ""
    lease_until: float = 0.0
    fencing_token: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DistributedCoordinator:
    """Registry + routing control plane for explicitly registered nodes."""

    def __init__(self, path: str | Path | None = None, *, heartbeat_timeout: float = 90.0):
        self.path = Path(path or config.resolve_path("data/distributed_nodes.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat_timeout = float(heartbeat_timeout)
        self._lock = threading.RLock()
        self.nodes: dict[str, HermusNode] = {}
        self.assignments: dict[str, DistributedAssignment] = {}
        self._load()

    def register_node(
        self,
        name: str,
        *,
        node_id: str | None = None,
        capabilities: list[str] | None = None,
        endpoint: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        node_id = str(node_id or f"node_{uuid.uuid4().hex[:12]}")
        with self._lock:
            existing = self.nodes.get(node_id)
            node = existing or HermusNode(id=node_id, name=str(name)[:120])
            node.name = str(name)[:120]
            node.capabilities = sorted({str(c)[:100] for c in (capabilities or []) if str(c).strip()})
            node.endpoint = str(endpoint)[:300]
            node.metadata = dict(metadata or {}) if isinstance(metadata, dict) else {}
            node.status = "online"
            node.last_heartbeat = time.time()
            self.nodes[node_id] = node
            self._save()
            return node.to_dict()

    def heartbeat(self, node_id: str, *, status: str = "online", capabilities: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            node = self.nodes.get(str(node_id))
            if node is None:
                raise KeyError("node not registered")
            node.status = str(status)[:30]
            if capabilities is not None:
                node.capabilities = sorted({str(c)[:100] for c in capabilities if str(c).strip()})
            node.last_heartbeat = time.time()
            self._save()
            return node.to_dict()

    def unregister(self, node_id: str) -> bool:
        with self._lock:
            removed = self.nodes.pop(str(node_id), None) is not None
            if removed:
                self._save()
            return removed

    def list_nodes(self) -> list[dict[str, Any]]:
        self._mark_stale()
        with self._lock:
            return [n.to_dict() for n in self.nodes.values()]

    def status(self) -> dict[str, Any]:
        nodes = self.list_nodes()
        return {
            "node_count": len(nodes),
            "online": sum(n["status"] == "online" for n in nodes),
            "stale": sum(n["status"] == "stale" for n in nodes),
            "nodes": nodes,
            "assignments": [a.to_dict() for a in self.assignments.values()],
            "emergency_stop": get_emergency_stop().active(),
        }

    def assign(self, job_id: str, *, capability: str = "", node_id: str | None = None) -> dict[str, Any]:
        if get_emergency_stop().active():
            return {"success": False, "error": "emergency_stop_active"}
        self._mark_stale()
        with self._lock:
            node = self.nodes.get(str(node_id)) if node_id else self._select(capability)
            if node is None:
                return {"success": False, "error": "no_healthy_node_with_capability", "capability": capability}
            if capability and capability not in node.capabilities:
                return {"success": False, "error": "node_lacks_capability"}
            existing = next(
                (a for a in self.assignments.values() if a.job_id == str(job_id) and a.status in ("assigned", "running")), None
            )
            if existing:
                return {"success": True, "assignment": existing.to_dict(), "deduplicated": True}
            node.fencing_token += 1
            lease_until = time.time() + self.heartbeat_timeout
            node.lease_until = lease_until
            assignment = DistributedAssignment(
                id=f"assign_{uuid.uuid4().hex[:12]}",
                job_id=str(job_id),
                node_id=node.id,
                capability=str(capability or ""),
                reason="explicit node" if node_id else "capability routing",
                lease_until=lease_until,
                fencing_token=node.fencing_token,
            )
            self.assignments[assignment.id] = assignment
            self._save()
            return {"success": True, "assignment": assignment.to_dict(), "node": node.to_dict()}

    def renew_assignment(self, assignment_id: str, *, node_id: str, fencing_token: int) -> dict[str, Any]:
        with self._lock:
            assignment = self.assignments.get(str(assignment_id))
            node = self.nodes.get(str(node_id))
            if not assignment or not node:
                return {"success": False, "error": "assignment_or_node_not_found"}
            if assignment.node_id != node.id or assignment.fencing_token != int(fencing_token):
                return {"success": False, "error": "stale_fencing_token"}
            if assignment.status not in ("assigned", "running"):
                return {"success": False, "error": "assignment_not_active"}
            lease = time.time() + self.heartbeat_timeout
            assignment.status = "running"
            assignment.lease_until = lease
            node.lease_until = lease
            self._save()
            return {"success": True, "assignment": assignment.to_dict()}

    def failover(self, assignment_id: str) -> dict[str, Any]:
        if get_emergency_stop().active():
            return {"success": False, "error": "emergency_stop_active"}
        self._mark_stale()
        with self._lock:
            assignment = self.assignments.get(str(assignment_id))
            if not assignment:
                return {"success": False, "error": "assignment_not_found"}
            if assignment.lease_until > time.time():
                return {"success": False, "error": "lease_still_active"}
            node = self._select(assignment.capability)
            if not node:
                return {"success": False, "error": "no_failover_node"}
            node.fencing_token += 1
            assignment.node_id = node.id
            assignment.fencing_token = node.fencing_token
            assignment.lease_until = time.time() + self.heartbeat_timeout
            assignment.status = "assigned"
            assignment.reason = "safe lease failover"
            self._save()
            return {"success": True, "assignment": assignment.to_dict(), "node": node.to_dict()}

    def complete_assignment(
        self, assignment_id: str, *, success: bool, node_id: str | None = None, fencing_token: int | None = None
    ) -> dict[str, Any]:
        with self._lock:
            assignment = self.assignments.get(str(assignment_id))
            if assignment is None:
                return {"success": False, "error": "assignment_not_found"}
            if node_id is not None and (
                assignment.node_id != str(node_id) or assignment.fencing_token != int(fencing_token or 0)
            ):
                return {"success": False, "error": "stale_fencing_token"}
            assignment.status = "succeeded" if success else "failed"
            assignment.lease_until = 0.0
            self._save()
            return {"success": True, "assignment": assignment.to_dict()}

    def _select(self, capability: str) -> HermusNode | None:
        candidates = [n for n in self.nodes.values() if n.status == "online" and (not capability or capability in n.capabilities)]
        if not candidates:
            return None
        return max(candidates, key=lambda n: n.last_heartbeat)

    def _mark_stale(self) -> None:
        now = time.time()
        changed = False
        with self._lock:
            for node in self.nodes.values():
                if node.status == "online" and now - node.last_heartbeat > self.heartbeat_timeout:
                    node.status = "stale"
                    changed = True
            if changed:
                self._save()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for row in data.get("nodes", []) if isinstance(data, dict) else []:
            if isinstance(row, dict) and row.get("id"):
                self.nodes[row["id"]] = HermusNode(
                    **{
                        k: row[k]
                        for k in (
                            "id",
                            "name",
                            "capabilities",
                            "endpoint",
                            "status",
                            "last_heartbeat",
                            "metadata",
                            "lease_until",
                            "fencing_token",
                        )
                        if k in row
                    }
                )
        for row in data.get("assignments", []) if isinstance(data, dict) else []:
            if isinstance(row, dict) and row.get("id"):
                self.assignments[row["id"]] = DistributedAssignment(
                    **{
                        k: row[k]
                        for k in (
                            "id",
                            "job_id",
                            "node_id",
                            "capability",
                            "status",
                            "created_at",
                            "reason",
                            "lease_until",
                            "fencing_token",
                        )
                        if k in row
                    }
                )

    def _save(self) -> None:
        payload = {
            "version": 1,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "assignments": [a.to_dict() for a in self.assignments.values()][-500:],
        }
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass


distributed = DistributedCoordinator()

__all__ = ["HermusNode", "DistributedAssignment", "DistributedCoordinator", "distributed"]
