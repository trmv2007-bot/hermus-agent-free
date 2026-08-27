"""Dependency-Aware Agent DAG for Hermus.

Orchestrates multi-agent teams via a structured Directed Acyclic Graph (DAG) with
dependency resolution, parallel stage execution, artifact passing, retry tracking,
and cycle prevention.
"""
from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from collections.abc import Callable


class DAGNodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


def _safe_serialize(obj: Any, seen: Optional[set[int]] = None) -> Any:
    if seen is None:
        seen = set()
    obj_id = id(obj)
    if obj_id in seen:
        return "<circular_ref>"
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, (list, tuple, set)):
        seen.add(obj_id)
        res = [_safe_serialize(x, seen) for x in obj]
        seen.remove(obj_id)
        return res
    if isinstance(obj, dict):
        seen.add(obj_id)
        res = {str(k): _safe_serialize(v, seen) for k, v in obj.items()}
        seen.remove(obj_id)
        return res
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        seen.add(obj_id)
        try:
            res = obj.to_dict()
        except Exception:
            res = str(obj)
        seen.remove(obj_id)
        return res
    return str(obj)


@dataclass
class DAGNode:
    id: str
    role: str
    goal: str
    dependencies: list[str] = field(default_factory=list)
    status: str = DAGNodeStatus.PENDING.value
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    assigned_model: Optional[str] = None
    retries: int = 0
    max_retries: int = 2
    execution_time_sec: float = 0.0
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "goal": self.goal,
            "dependencies": list(self.dependencies),
            "status": self.status,
            "inputs": _safe_serialize(self.inputs),
            "outputs": _safe_serialize(self.outputs),
            "artifacts": list(self.artifacts),
            "assigned_model": self.assigned_model,
            "retries": self.retries,
            "max_retries": self.max_retries,
            "execution_time_sec": self.execution_time_sec,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAGNode:
        return cls(
            id=data["id"],
            role=data["role"],
            goal=data["goal"],
            dependencies=data.get("dependencies", []),
            status=data.get("status", DAGNodeStatus.PENDING.value),
            inputs=data.get("inputs", {}),
            outputs=data.get("outputs", {}),
            artifacts=data.get("artifacts", []),
            assigned_model=data.get("assigned_model"),
            retries=data.get("retries", 0),
            max_retries=data.get("max_retries", 2),
            execution_time_sec=data.get("execution_time_sec", 0.0),
            error=data.get("error"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )


class AgentDAG:
    """Manages DAG graph state, topological ordering, and parallel stage execution."""

    def __init__(self, name: str = "Agent Team Workflow"):
        self.name = name
        self.nodes: dict[str, DAGNode] = {}
        self.created_at = datetime.now().isoformat()

    def add_node(
        self,
        node_id: str,
        role: str,
        goal: str,
        dependencies: Optional[list[str]] = None,
        inputs: Optional[dict[str, Any]] = None,
        assigned_model: Optional[str] = None,
        max_retries: int = 2,
    ) -> DAGNode:
        deps = dependencies or []
        node = DAGNode(
            id=node_id,
            role=role,
            goal=goal,
            dependencies=deps,
            status=DAGNodeStatus.READY.value if not deps else DAGNodeStatus.PENDING.value,
            inputs=inputs or {},
            assigned_model=assigned_model,
            max_retries=max_retries,
        )
        self.nodes[node_id] = node
        return node

    def add_edge(self, parent_id: str, child_id: str) -> None:
        if parent_id not in self.nodes:
            raise ValueError(f"Parent node '{parent_id}' does not exist in DAG")
        if child_id not in self.nodes:
            raise ValueError(f"Child node '{child_id}' does not exist in DAG")
        if parent_id not in self.nodes[child_id].dependencies:
            self.nodes[child_id].dependencies.append(parent_id)

    def validate(self) -> bool:
        try:
            self.topological_sort()
            return True
        except ValueError:
            return False

    def topological_sort(self) -> list[DAGNode]:
        in_degree: dict[str, int] = {nid: 0 for nid in self.nodes}
        adj: dict[str, list[str]] = collections.defaultdict(list)

        for nid, node in self.nodes.items():
            for dep in node.dependencies:
                if dep not in self.nodes:
                    raise ValueError(f"Node '{nid}' depends on non-existent node '{dep}'")
                adj[dep].append(nid)
                in_degree[nid] += 1

        queue = collections.deque([nid for nid, deg in in_degree.items() if deg == 0])
        ordered: list[DAGNode] = []

        while queue:
            curr = queue.popleft()
            ordered.append(self.nodes[curr])
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(ordered) != len(self.nodes):
            raise ValueError("Cycle detected in Agent DAG dependencies")

        return ordered

    def get_ready_nodes(self) -> list[DAGNode]:
        ready: list[DAGNode] = []
        for node in self.nodes.values():
            if node.status in (DAGNodeStatus.PENDING.value, DAGNodeStatus.READY.value):
                deps_satisfied = all(
                    self.nodes[dep].status == DAGNodeStatus.COMPLETED.value
                    for dep in node.dependencies
                )
                deps_failed = any(
                    self.nodes[dep].status in (DAGNodeStatus.FAILED.value, DAGNodeStatus.BLOCKED.value)
                    for dep in node.dependencies
                )
                if deps_failed:
                    node.status = DAGNodeStatus.SKIPPED.value
                    node.error = "Upstream dependency failed"
                elif deps_satisfied:
                    node.status = DAGNodeStatus.READY.value
                    ready.append(node)
        return ready

    def execute_dag(
        self,
        node_executor: Callable[[Any, dict[str, Any]], dict[str, Any]],
        max_rounds: int = 50,
    ) -> dict[str, Any]:
        self.validate()
        rounds = 0

        while rounds < max_rounds:
            rounds += 1
            ready_nodes = self.get_ready_nodes()

            if not ready_nodes:
                break

            for node in ready_nodes:
                node.status = DAGNodeStatus.RUNNING.value
                node.started_at = datetime.now().isoformat()
                t0 = time.time()

                parent_context: dict[str, Any] = {}
                for dep in node.dependencies:
                    parent = self.nodes[dep]
                    parent_context[dep] = {
                        "outputs": _safe_serialize(parent.outputs),
                        "artifacts": list(parent.artifacts),
                    }

                try:
                    # Invoke executor (supports (node, ctx) or (goal, ctx))
                    result = node_executor(node, parent_context)
                    t1 = time.time()
                    node.execution_time_sec = round(t1 - t0, 3)
                    node.finished_at = datetime.now().isoformat()

                    if result.get("blocked"):
                        node.status = DAGNodeStatus.BLOCKED.value
                        node.error = str(result.get("blocker_reason") or "External prerequisite or authorization required")
                        node.outputs = _safe_serialize(result)
                    elif result.get("success", True) and not result.get("error"):
                        node.status = DAGNodeStatus.COMPLETED.value
                        node.outputs = _safe_serialize(result.get("outputs", result))
                        node.artifacts = list(result.get("artifacts", []))
                    else:
                        node.error = str(result.get("error", "Execution failed"))
                        node.outputs = _safe_serialize(result)
                        if node.retries < node.max_retries:
                            node.retries += 1
                            node.status = DAGNodeStatus.READY.value
                        else:
                            node.status = DAGNodeStatus.FAILED.value
                except Exception as e:
                    t1 = time.time()
                    node.execution_time_sec = round(t1 - t0, 3)
                    node.finished_at = datetime.now().isoformat()
                    node.error = str(e)
                    if node.retries < node.max_retries:
                        node.retries += 1
                        node.status = DAGNodeStatus.READY.value
                    else:
                        node.status = DAGNodeStatus.FAILED.value

        completed = sum(1 for n in self.nodes.values() if n.status == DAGNodeStatus.COMPLETED.value)
        failed = sum(1 for n in self.nodes.values() if n.status == DAGNodeStatus.FAILED.value)
        blocked = sum(1 for n in self.nodes.values() if n.status == DAGNodeStatus.BLOCKED.value)

        return {
            "success": completed == len(self.nodes) and len(self.nodes) > 0,
            "total_nodes": len(self.nodes),
            "completed": completed,
            "failed": failed,
            "blocked": blocked,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "created_at": self.created_at,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDAG:
        dag = cls(name=data.get("name", "Agent DAG"))
        dag.created_at = data.get("created_at", datetime.now().isoformat())
        nodes_data = data.get("nodes", {})
        for nid, nd in nodes_data.items():
            dag.nodes[nid] = DAGNode.from_dict(nd)
        return dag


def create_standard_mission_dag(task: str) -> AgentDAG:
    dag = AgentDAG(name=f"SWE Mission: {task[:40]}")
    dag.add_node("research", "researcher", f"Research requirements and background for: {task}")
    dag.add_node("architecture", "architect", f"Design architecture and file structure for: {task}", dependencies=["research"])
    dag.add_node("implementation", "coder", f"Implement code, tests, and configurations for: {task}", dependencies=["architecture"])
    dag.add_node("code_review", "code_reviewer", "Review code changes for correctness and maintainability", dependencies=["implementation"])
    dag.add_node("security_audit", "security_auditor", "Audit code and configuration for security vulnerabilities", dependencies=["implementation"])
    dag.add_node("integration", "integrator", "Build and integrate components", dependencies=["code_review", "security_audit"])
    dag.add_node("verification", "verifier", f"Run domain verifiers and test suite for final proof: {task}", dependencies=["integration"])
    return dag
