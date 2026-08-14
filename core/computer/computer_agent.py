"""Autonomous computer agent — Plan → Act → Record → Verify → Repair.

This is where Hermus stops *seeing* the computer and starts *operating* it.

:class:`ComputerAgent` accepts a natural-language task, plans a sequence of
desktop steps (recalling a previously-learned skill when one matches), executes
each step through the gated :class:`ComputerActionController`, records the
exact BEFORE/AFTER screen boundaries, verifies the expected visual transition,
and diagnoses/repairs failures instead of blindly retrying.  A full run
produces an evidence bundle (``recording.mp4``, ``timeline.json``,
``actions.json``, ``verification.json``, ``result.json``, ``summary.md``) and,
on success, promotes the procedure to a reusable skill.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .controller import ComputerActionController
from .permissions import RecordingPolicy, recording_policy
from .recorder import ImageGrabSource, ScreenRecorder
from .skills import ComputerSkillStore
from .state_machine import VisualState, VisualStateMachine, dispatch_action
from .timeline import TaskArtifacts, Timeline
from .verifier import ScreenVerifier
from .video_analyzer import VideoAnalyzer
from .video_writer import VideoWriter
from .watcher import ScreenWatcher


def _slug(value: str) -> str:
    import re

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "").strip()).strip(".-")
    return safe[:40] or "task"


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _duration_text(seconds: float) -> str:
    return f"{max(0.0, seconds):.1f}s"


class ComputerAgent:
    """Run a user task as a recorded, verified, repairable desktop procedure."""

    def __init__(
        self,
        controller: Optional[ComputerActionController] = None,
        recorder: Optional[ScreenRecorder] = None,
        planner: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
        analyzer: Optional[VideoAnalyzer] = None,
        verifier: Optional[ScreenVerifier] = None,
        policy: Optional[RecordingPolicy] = None,
        skills: Optional[ComputerSkillStore] = None,
        learn_skills: bool = True,
        max_retries: int = 2,
    ):
        self.controller = controller or ComputerActionController()
        self.recorder = recorder or ScreenRecorder(source=ImageGrabSource())
        self.planner = planner or self._default_plan
        self.analyzer = analyzer
        self.verifier = verifier or ScreenVerifier()
        self.policy = policy or recording_policy
        self.skills = skills or ComputerSkillStore()
        self.learn_skills = learn_skills
        self.max_retries = max_retries

    # -- planning -------------------------------------------------------
    def _default_plan(self, task: str) -> List[Dict[str, Any]]:
        """Recall a matching skill, else fall back to a single vision action."""
        skill = self.skills.recall(task)
        if skill is not None and skill.procedure:
            plan = []
            for step in skill.procedure:
                plan.append({
                    "name": step.get("name", "step"),
                    "expected": step.get("expected", ""),
                    "action": step.get("action"),
                })
            plan[0].setdefault("_recalled_from", skill.name)
            return plan
        return [{
            "name": "act",
            "expected": "",
            "action": {"kind": "click_target", "target": task},
        }]

    # -- convenience tools ----------------------------------------------
    def find_on_screen(self, target: str) -> Dict[str, Any]:
        return self.controller.find_on_screen(target)

    def click_target(self, target: str) -> Dict[str, Any]:
        return self.controller.click_target(target)

    def wait_until(self, condition: str, timeout: float = 60.0) -> Dict[str, Any]:
        """Expose ScreenWatcher as a first-class agent tool."""
        watcher = ScreenWatcher(self.recorder, analyzer=self.analyzer or VideoAnalyzer())
        return watcher.watch(condition, timeout=timeout, start_if_needed=False)

    # -- main loop ------------------------------------------------------
    def run(self, task: str, task_id: Optional[str] = None) -> Dict[str, Any]:
        started_monotonic = time.monotonic()
        task_id = task_id or f"{datetime.now().strftime('%Y-%m-%d')}-{_slug(task)}"
        artifacts = TaskArtifacts(task_id, root=str(self.policy.root))
        task_dir = artifacts.directory
        recording_path = task_dir / "recording.mp4"

        # Stream to disk when FFmpeg is available; otherwise stay RAM-bounded.
        ffmpeg = VideoWriter.available()["available"]
        started = self.recorder.start(output_path=str(recording_path)) if ffmpeg else self.recorder.start()
        if not started.get("success"):
            # A failed writer must not block the whole loop; degrade gracefully.
            started = self.recorder.start()

        timeline = Timeline(task=task, recording=str(recording_path) if ffmpeg else None)
        timeline.add(0.0, "task_start", f"Task: {task}", 1.0, _now())

        plan = self.planner(task) or []
        states = VisualStateMachine.plan_to_states(plan)

        machine = VisualStateMachine(
            controller=self.controller,
            recorder=self.recorder,
            wait_until=self.wait_until,
            execute=lambda spec: dispatch_action(self.controller, spec),
            verify=self._verify,
            max_retries=self.max_retries,
        )
        report = machine.run(states, timeout_per_state=60.0)

        # Collect evidence from the state trace.
        actions: List[Dict[str, Any]] = []
        verifications: List[Dict[str, Any]] = []
        retries = 0
        for visited in report.get("states_visited", []):
            action = visited.get("action")
            verification = visited.get("verification")
            if isinstance(action, dict) and action:
                actions.append(action)
                timeline.add(
                    0.0,
                    action.get("action", "action"),
                    action.get("description") or str(action.get("action", "action")),
                    action.get("confidence", 0.0),
                    action.get("ts"),
                    {"action": action.get("action"), "args": action.get("args", {})},
                )
            if isinstance(verification, dict) and verification:
                verifications.append(verification)
                timeline.add(
                    0.0,
                    "verify",
                    verification.get("detail") or ("PASS" if verification.get("ok") else "FAIL"),
                    verification.get("confidence", 0.0),
                    None,
                    {"ok": bool(verification.get("ok"))},
                )
                if not verification.get("ok"):
                    retries += 1

        stopped = self.recorder.stop()
        recording = (stopped.get("video") or {}).get("path") if ffmpeg else None
        if recording is None and ffmpeg and (task_dir / "recording.mp4").exists():
            recording = str(task_dir / "recording.mp4")

        success = bool(report.get("success"))
        duration = time.monotonic() - started_monotonic
        timeline.add(duration, "task_end", "SUCCESS" if success else "FAILURE", 1.0 if success else 0.0, _now())

        # Persist the evidence bundle (recording + timeline/actions/result…).
        bundle = artifacts.write(
            timeline=timeline,
            events=[],
            actions=actions,
            result={"success": success, "task": task, "duration": duration,
                    "actions": len(actions), "retries": retries, "result": "SUCCESS" if success else "FAILURE"},
            recording_path=recording,
        )

        # Extra artifacts from the agent spec: verification.json + summary.md.
        verification_path = task_dir / "verification.json"
        verification_path.write_text(
            __import__("json").dumps(verifications, indent=2, default=str), encoding="utf-8"
        )
        summary_path = task_dir / "summary.md"
        summary_path.write_text(self._summary(task, success, duration, actions, verifications, retries, timeline), encoding="utf-8")
        self.policy.secure(verification_path)
        self.policy.secure(summary_path)

        # Promote the successful procedure to a skill.
        skill = None
        if success and self.learn_skills:
            procedure = []
            for visited in report.get("states_visited", []):
                if isinstance(visited.get("action"), dict) and visited["action"]:
                    procedure.append({
                        "name": visited.get("state"),
                        "action": visited["action"].get("action"),
                        "args": visited["action"].get("args", {}),
                        "expected": "",
                        "verification": visited.get("verification"),
                        "evidence": {"recording": recording, "offset": visited["action"].get("ts")},
                    })
            skill = self.skills.save_skill(
                task,
                procedure,
                evidence={"recording": recording, "task_id": task_id},
            )

        return {
            "success": success,
            "task": task,
            "task_id": task_id,
            "duration": duration,
            "duration_text": _duration_text(duration),
            "actions": actions,
            "verifications": verifications,
            "retries": retries,
            "result": "SUCCESS" if success else "FAILURE",
            "states_visited": report.get("states_visited", []),
            "timeline": timeline.to_dict(),
            "timeline_text": timeline.render_text(),
            "recording": recording,
            "artifacts": bundle,
            "verification_path": str(verification_path),
            "summary_path": str(summary_path),
            "skill": skill,
            "error": report.get("error"),
        }

    # -- verification ---------------------------------------------------
    def _verify(self, before: Any, after: Any, expected: str) -> Dict[str, Any]:
        if self.analyzer is not None and self.analyzer.vision_model is not None:
            result = self.analyzer.evaluate_transition(before, after, expected or "the action had its intended effect")
            result["ok"] = bool(result.get("matched"))
            return result
        return self.verifier.verify(before, after, expected)

    # -- summary --------------------------------------------------------
    @staticmethod
    def _summary(task: str, success: bool, duration: float, actions: List[Dict[str, Any]],
                 verifications: List[Dict[str, Any]], retries: int, timeline: Timeline) -> str:
        lines = [
            f"# Task: {task}",
            "",
            f"Duration: {_duration_text(duration)}",
            f"Actions: {len(actions)}",
            f"Visual verifications: {len(verifications)}",
            f"Retries: {retries}",
            "",
            f"Result: {'SUCCESS' if success else 'FAILURE'}",
            "",
            "Timeline:",
        ]
        for event in timeline.events:
            minutes, seconds = divmod(max(0, int(event.offset)), 60)
            lines.append(f"{minutes:02d}:{seconds:02d} {event.description}")
        return "\n".join(lines) + "\n"
