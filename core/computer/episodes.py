"""Episode Memory — replayable task recordings with action traces.

After every task Hermus stores an "episode": the full recording, timeline,
actions, failures, repairs, and outcome.  This lets Hermus answer:

    "How did I do this last time?"

by retrieving the successful visual/action trajectory rather than just text
memory.  Episodes also feed the skill optimization system and the benchmark
evaluator.

An episode stores:

    ├── task              — description
    ├── task_id           — unique id
    ├── outcome           — SUCCESS / FAILURE / CANCELLED
    ├── started / ended   — timestamps
    ├── duration          — wall-clock seconds
    ├── recording         — path to screen video (if available)
    ├── timeline          — structured event list (offsets, types, descriptions)
    ├── actions           — all action specs and results
    ├── verifications     — verification results per action
    ├── repairs           — repair attempts and outcomes
    ├── plan              — the original plan graph
    ├── world_states      — world state snapshots at key points
    └── metrics           — counts of actions/retries/repairs/verifications
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import builtins


def _now() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass
class Episode:
    """One recorded task execution with full action trace."""

    task_id: str
    task: str
    outcome: str = "UNKNOWN"  # SUCCESS / FAILURE / CANCELLED / INTERRUPTED
    started: str = field(default_factory=_now)
    ended: Optional[str] = None
    duration: float = 0.0
    recording: Optional[str] = None
    plan: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    verifications: list[dict[str, Any]] = field(default_factory=list)
    repairs: list[dict[str, Any]] = field(default_factory=list)
    diagnoses: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    world_states: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    failure: Optional[dict[str, Any]] = None
    metrics: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    version: int = 2

    def compute_metrics(self) -> dict[str, Any]:
        """Auto-compute metrics from recorded data."""
        actions_ok = sum(
            1 for a in self.actions
            if a.get("outcome") == "success"
        )
        actions_total = max(len(self.actions), 1)
        repairs_success = sum(
            1 for r in self.repairs
            if r.get("success") or r.get("outcome") == "success"
        )
        repairs_total = max(len(self.repairs), 1)
        retries = sum(
            1 for a in self.actions
            if int(a.get("attempt", 1) or 1) > 1
        )

        self.metrics = {
            "total_actions": len(self.actions),
            "action_success_rate": round(actions_ok / actions_total, 4),
            "total_repairs": len(self.repairs),
            "repair_success_rate": round(repairs_success / repairs_total, 4) if self.repairs else 1.0,
            "total_verifications": len(self.verifications),
            "retries": retries,
            "duration_seconds": round(self.duration, 2),
            "actions_per_minute": round(
                len(self.actions) / (self.duration / 60.0), 2
            ) if self.duration > 0 else 0.0,
        }
        return self.metrics

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task": self.task,
            "outcome": self.outcome,
            "started": self.started,
            "ended": self.ended,
            "duration": round(self.duration, 2),
            "recording": self.recording,
            "plan": self.plan,
            "actions": self.actions,
            "verifications": self.verifications,
            "repairs": self.repairs,
            "diagnoses": self.diagnoses,
            "timeline": self.timeline,
            "world_states": self.world_states,
            "error": self.error,
            "failure": self.failure,
            "metrics": self.metrics or self.compute_metrics(),
            "tags": self.tags,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Episode":
        return cls(
            task_id=str(data.get("task_id", "")),
            task=str(data.get("task", "")),
            outcome=str(data.get("outcome", "UNKNOWN")),
            started=str(data.get("started", _now())),
            ended=data.get("ended"),
            duration=float(data.get("duration", 0.0)),
            recording=data.get("recording"),
            plan=list(data.get("plan", [])),
            actions=list(data.get("actions", [])),
            verifications=list(data.get("verifications", [])),
            repairs=list(data.get("repairs", [])),
            diagnoses=list(data.get("diagnoses", [])),
            timeline=list(data.get("timeline", [])),
            world_states=list(data.get("world_states", [])),
            error=data.get("error"),
            failure=data.get("failure"),
            metrics=dict(data.get("metrics", {})),
            tags=list(data.get("tags", [])),
            version=int(data.get("version", 1)),
        )

    @classmethod
    def from_task_result(cls, task_id: str, task: str, result: dict[str, Any]) -> "Episode":
        """Build an Episode from a ComputerAgent.run() result dict."""
        started = result.get("checkpoint", {}).get("created_at") or result.get("started", _now())
        timeline_raw = result.get("timeline", {}).get("events", []) if isinstance(
            result.get("timeline"), dict) else result.get("timeline", [])
        success = bool(result.get("success"))

        return cls(
            task_id=task_id,
            task=task,
            outcome="SUCCESS" if success else (
                "CANCELLED" if str(result.get("result", "")).upper() == "CANCELLED" else "FAILURE"
            ),
            started=started,
            ended=_now(),
            duration=float(result.get("duration", 0.0)),
            recording=result.get("recording"),
            plan=list(result.get("checkpoint", {}).get("plan", result.get("plan", []))),
            actions=list(result.get("actions", [])),
            verifications=list(result.get("verifications", [])),
            repairs=list(result.get("repairs", [])),
            diagnoses=list(result.get("diagnoses", [])),
            timeline=timeline_raw,
            world_states=list(result.get("world_state", {}).get("observations", [])),
            error=result.get("error"),
            failure=result.get("failure"),
            tags=[result.get("result", "").lower()] if result.get("result") else [],
        )


class EpisodeStore:
    """Persistent episode memory with search/recall.

    Episodes are stored as JSON files in ``data/episodes/``, one per task.
    The store supports:

    - save / load an episode
    - list all episodes (sorted recency)
    - search by task or outcome
    - recall the most recent episode for a task description
    - aggregate statistics across all episodes
    """

    def __init__(self, root: Optional[str] = None):
        root_path = Path(root).expanduser() if root else (
            Path(__file__).resolve().parents[2] / "data" / "episodes"
        )
        self.root = root_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id)[:120] or "episode"
        return self.root / f"{safe}.json"

    def save(self, episode: Episode) -> str:
        """Save an episode and return the path."""
        episode.ended = episode.ended or _now()
        episode.compute_metrics()
        path = self._path(episode.task_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(episode.to_dict(), indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
        return str(path)

    def save_from_result(self, task_id: str, task: str, result: dict[str, Any]) -> str:
        """Convenience: build and save an episode from a run result."""
        episode = Episode.from_task_result(task_id, task, result)
        return self.save(episode)

    def load(self, task_id: str) -> Optional[Episode]:
        """Load an episode by task_id."""
        path = self._path(task_id)
        if not path.exists():
            return None
        try:
            return Episode.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list(
        self,
        limit: int = 50,
        outcome: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> builtins.list[dict[str, Any]]:
        """List episodes as summaries, newest first.

        Args:
            limit: max episodes to return
            outcome: filter by outcome (SUCCESS/FAILURE/CANCELLED)
            tag: filter by tag
        """
        episodes: list[Episode] = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                ep = Episode.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            if outcome and ep.outcome.upper() != outcome.upper():
                continue
            if tag and tag.lower() not in [t.lower() for t in ep.tags]:
                continue
            episodes.append(ep)
            if len(episodes) >= limit:
                break

        return [
            {
                "task_id": ep.task_id,
                "task": ep.task,
                "outcome": ep.outcome,
                "duration": round(ep.duration, 2),
                "started": ep.started,
                "ended": ep.ended,
                "action_count": len(ep.actions),
                "repair_count": len(ep.repairs),
                "retries": ep.metrics.get("retries", 0),
                "success_rate": ep.metrics.get("action_success_rate", 0),
                "tags": ep.tags,
                "recording": ep.recording,
            }
            for ep in episodes
        ]

    def search(self, query: str, limit: int = 10) -> builtins.list[dict[str, Any]]:
        """Search episodes by task description text."""
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored: list[tuple] = []
        for path in self.root.glob("*.json"):
            try:
                ep = Episode.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            task_words = set(re.findall(r"[a-z0-9]+", ep.task.lower()))
            overlap = len(words & task_words)
            if overlap > 0:
                scored.append((overlap, ep))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "task_id": ep.task_id,
                "task": ep.task,
                "outcome": ep.outcome,
                "duration": round(ep.duration, 2),
                "action_count": len(ep.actions),
            }
            for _, ep in scored[:limit]
        ]

    def recall(self, task: str, min_success_rate: float = 0.5) -> Optional[Episode]:
        """Recall the most recent SUCCESSFUL episode for a similar task."""
        results = self.search(task, limit=5)
        for r in results:
            ep = self.load(r["task_id"])
            if ep and ep.outcome == "SUCCESS":
                return ep
        # Fall back to any episode
        for r in results:
            ep = self.load(r["task_id"])
            if ep:
                return ep
        return None

    def recall_trajectory(self, task: str) -> Optional[dict[str, Any]]:
        """Recall just the visual/action trajectory (for replay).

        Returns a minimal dict with just the essential replay data:
        plan, key actions, verifications, repairs.
        """
        ep = self.recall(task)
        if ep is None:
            return None
        return {
            "task": ep.task,
            "outcome": ep.outcome,
            "plan": ep.plan,
            "action_count": len(ep.actions),
            "repair_count": len(ep.repairs),
            "timeline_events": len(ep.timeline),
            "duration": ep.duration,
        }

    def stats(self) -> dict[str, Any]:
        """Aggregate statistics across all episodes."""
        episodes = []
        for path in self.root.glob("*.json"):
            try:
                ep = Episode.from_dict(json.loads(path.read_text(encoding="utf-8")))
                episodes.append(ep)
            except Exception:
                continue

        if not episodes:
            return {
                "total": 0,
                "success": 0,
                "failure": 0,
                "cancelled": 0,
                "avg_duration": 0.0,
                "avg_action_success_rate": 0.0,
                "avg_repair_success_rate": 0.0,
                "total_actions": 0,
                "total_repairs": 0,
            }

        return {
            "total": len(episodes),
            "success": sum(1 for e in episodes if e.outcome == "SUCCESS"),
            "failure": sum(1 for e in episodes if e.outcome == "FAILURE"),
            "cancelled": sum(1 for e in episodes if e.outcome in ("CANCELLED", "INTERRUPTED")),
            "avg_duration": round(
                sum(e.duration for e in episodes) / len(episodes), 2
            ),
            "avg_action_success_rate": round(
                sum(e.metrics.get("action_success_rate", 0) for e in episodes)
                / len(episodes), 4
            ),
            "avg_repair_success_rate": round(
                sum(e.metrics.get("repair_success_rate", 1.0) for e in episodes)
                / len(episodes), 4
            ),
            "total_actions": sum(len(e.actions) for e in episodes),
            "total_repairs": sum(len(e.repairs) for e in episodes),
        }

    def delete(self, task_id: str) -> bool:
        """Delete an episode by task_id."""
        path = self._path(task_id)
        if path.exists():
            path.unlink()
            return True
        # Clean up directory with all artifacts
        task_dir = self.root / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir)
            return True
        return False

    def clear(self) -> int:
        """Delete all episodes. Returns count removed."""
        count = 0
        for path in self.root.glob("*.json"):
            path.unlink()
            count += 1
        return count


# Global singleton
_episode_store: Optional[EpisodeStore] = None


def get_episode_store() -> EpisodeStore:
    global _episode_store
    if _episode_store is None:
        _episode_store = EpisodeStore()
    return _episode_store


def record_episode(task_id: str, task: str, result: dict[str, Any]) -> str:
    """Convenience: record a task result as an episode."""
    return get_episode_store().save_from_result(task_id, task, result)