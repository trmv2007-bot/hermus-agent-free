"""Dependency-Aware Agent DAG for Hermus.

Orchestrates multi-agent teams via a structured Directed Acyclic Graph (DAG) with
dependency resolution, parallel stage execution, artifact passing, retry tracking,
and cycle prevention.
"""
from __future__ import annotations

import collections
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set


class DAGNodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


@dataclass
class DAGNode:
    id: str
    role: str
    goal: str
    dependencies: List[str] = field(default_factory=list)
    status: str = DAGNodeStatus.PENDING.value
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)
    assigned_model: Optional[str] = None
    retries: int = 0
    max_retries: int = 2
    execution_time_sec: float = 0.0
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DAGNode:
        return cls(**data)


class AgentDAG:
    """Manages DAG graph state, topological ordering, and parallel stage execution."""

    def __init__(self, name: str = "Agent Team Workflow"):
        self.name = name
        self.nodes: Dict[str, DAGNode] = {}
        self.created_at = datetime.now().isoformat()

    def add_node(
        self,
        node_id: str,
        role: str,
        goal: str,
        dependencies: Optional[List[str]] = None,
        inputs: Optional[Dict[str, Any]] = None,
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
        """Validate DAG integrity and check for cyclic dependencies."""
        try:
            self.topological_sort()
            return True
        except ValueError:
            return False

    def topological_sort(self) -> List[DAGNode]:
        """Return nodes in topological dependency order (Kahn's algorithm)."""
        in_degree: Dict[str, int] = {nid: 0 for nid in self.nodes}
        adj: Dict[str, List[str]] = collections.defaultdict(list)

        for nid, node in self.nodes.items():
            for dep in node.dependencies:
                if dep not in self.nodes:
                    raise ValueError(f"Node '{nid}' depends on non-existent node '{dep}'")
                adj[dep].append(nid)
                in_degree[nid] += 1

        queue = collections.deque([nid for nid, deg in in_degree.items() if deg == 0])
        ordered: List[DAGNode] = []

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

    def get_ready_nodes(self) -> List[DAGNode]:
        """Find pending nodes whose upstream dependencies have all completed successfully."""
        ready: List[DAGNode] = []
        for node in self.nodes.values():
            if node.status in (DAGNodeStatus.PENDING.value, DAGNodeStatus.READY.value):
                # Check if all dependencies are completed
                deps_satisfied = all(
                    self.nodes[dep].status == DAGNodeStatus.COMPLETED.value
                    for dep in node.dependencies
                )
                deps_failed = any(
                    self.nodes[dep].status in (DAGNodeStatus.FAILED.value, DAGNodeStatus.BLOCKED.value)
                    for dep in node.dependencies
                )
                if deps_failed:
                    node.status = DAGNodeStatus.BLOCKED.value
                    node.error = "Upstream dependency failed"
                elif deps_satisfied:
                    node.status = DAGNodeStatus.READY.value
                    ready.append(node)
        return ready

    def execute_dag(
        self,
        node_executor: Callable[[DAGNode, Dict[str, Any]], Dict[str, Any]],
        max_rounds: int = 50,
    ) -> Dict[str, Any]:
        """Execute DAG until all nodes reach terminal state or max rounds exceeded."""
        self.validate()
        rounds = 0

        while rounds < max_rounds:
            rounds += 1
            ready_nodes = self.get_ready_nodes()

            # If no nodes are ready, check if we are finished
            if not ready_nodes:
                active = [n for n in self.nodes.values() if n.status in (DAGNodeStatus.PENDING.value, DAGNodeStatus.READY.value, DAGNodeStatus.RUNNING.value)]
                if not active:
                    break  # All nodes reached terminal status
                # If there are active nodes but none ready, we are blocked
                for n in active:
                    n.status = DAGNodeStatus.BLOCKED.value
                break

            for node in ready_nodes:
                node.status = DAGNodeStatus.RUNNING.value
                node.started_at = datetime.now().isoformat()
                t0 = time.time()

                # Gather context from parent nodes
                parent_context: Dict[str, Any] = {}
                for dep in node.dependencies:
                    parent = self.nodes[dep]
                    parent_context[dep] = {
                        "outputs": parent.outputs,
                        "artifacts": parent.artifacts,
                    }

                try:
                    result = node_executor(node, parent_context)
                    t1 = time.time()
                    node.execution_time_sec = round(t1 - t0, 3)
                    node.finished_at = datetime.now().isoformat()

                    if result.get("success", True) and not result.get("error"):
                        node.status = DAGNodeStatus.COMPLETED.value
                        node.outputs = result.get("outputs", result)
                        node.artifacts = result.get("artifacts", [])
                    else:
                        node.error = str(result.get("error", "Execution failed"))
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "created_at": self.created_at,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentDAG:
        dag = cls(name=data.get("name", "Agent DAG"))
        dag.created_at = data.get("created_at", datetime.now().isoformat())
        nodes_data = data.get("nodes", {})
        for nid, nd in nodes_data.items():
            dag.nodes[nid] = DAGNode.from_dict(nd)
        return dag


def create_standard_mission_dag(task: str) -> AgentDAG:
    """Build the standard 7-stage software development DAG recommended in the roadmap:
    Mission → (Research & Architecture) → Implementation → (Security & Code Review) → Integration → Verification → Repair/Complete
    """
    dag = AgentDAG(name=f"SWE Mission: {task[:40]}")
    dag.add_node("research", "researcher", f"Research requirements and background for: {task}")
    dag.add_node("architecture", "architect", f"Design architecture and file structure for: {task}", dependencies=["research"])
    dag.add_node("implementation", "coder", f"Implement code, tests, and configurations for: {task}", dependencies=["architecture"])
    dag.add_node("code_review", "code_reviewer", "Review code changes for correctness and maintainability", dependencies=["implementation"])
    dag.add_node("security_audit", "security_auditor", "Audit code and configuration for security vulnerabilities", dependencies=["implementation"])
    dag.add_node("integration", "integrator", "Build and integrate components", dependencies=["code_review", "security_audit"])
    dag.add_node("verification", "verifier", "Run domain verifiers and test suite for final proof", dependencies=["integration"])
    return dag
