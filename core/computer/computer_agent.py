"""Persistent autonomous desktop agent: plan → act → verify → repair → resume."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .controller import ComputerActionController
from .events import machine_event, publish
from .permissions import RecordingPolicy, recording_policy
from .planner import ComputerPlanner
from .repair import RepairEngine
from .replanner import AdaptiveReplanner, ReplanContext
from .recorder import ImageGrabSource, ScreenRecorder
from .skills import ComputerSkillStore
from .state_machine import VisualStateMachine, dispatch_action
from .task_control import get_task_control, TaskControlState
from .task_store import TaskStore
from .timeline import TaskArtifacts, Timeline
from .verifier import ScreenVerifier
from .video_analyzer import VideoAnalyzer
from .video_writer import VideoWriter
from .watcher import ScreenWatcher
from .world_state import WorldState


def _slug(value: str) -> str:
    import re

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "").strip()).strip(".-")
    return safe[:40] or "task"


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _duration_text(seconds: float) -> str:
    return f"{max(0.0, seconds):.1f}s"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


class ComputerAgent:
    """Run, checkpoint, repair, learn from, and resume a desktop task."""

    def __init__(
        self,
        controller: Optional[ComputerActionController] = None,
        recorder: Optional[ScreenRecorder] = None,
        planner: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
        analyzer: Optional[VideoAnalyzer] = None,
        verifier: Optional[ScreenVerifier] = None,
        repair_engine: Optional[RepairEngine] = None,
        policy: Optional[RecordingPolicy] = None,
        skills: Optional[ComputerSkillStore] = None,
        task_store: Optional[TaskStore] = None,
        world_state: Optional[WorldState] = None,
        learn_skills: bool = True,
        max_retries: int = 2,
    ):
        self.controller = controller or ComputerActionController()
        self.recorder = recorder or ScreenRecorder(source=ImageGrabSource())
        self.policy = policy or recording_policy
        self.skills = skills or ComputerSkillStore()
        self.world_state = world_state or WorldState()
        self.planner_engine = ComputerPlanner(skills=self.skills, world_state=self.world_state)
        self.planner = planner
        self.analyzer = analyzer
        self.verifier = verifier or ScreenVerifier()
        self.repair_engine = repair_engine or RepairEngine()
        self.replanner = AdaptiveReplanner()
        self.task_store = task_store or TaskStore(str(self.policy.root))
        self.learn_skills = learn_skills
        self.max_retries = max_retries

    # -- convenience tools ----------------------------------------------
    def find_on_screen(self, target: str) -> Dict[str, Any]:
        return self.controller.find_on_screen(target)

    def click_target(self, target: str) -> Dict[str, Any]:
        return self.controller.click_target(target)

    def wait_until(self, condition: str, timeout: float = 60.0) -> Dict[str, Any]:
        watcher = ScreenWatcher(self.recorder, analyzer=self.analyzer or VideoAnalyzer())
        return watcher.watch(condition, timeout=timeout, start_if_needed=False)

    # -- planning / resume ----------------------------------------------
    def _make_plan(self, task: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if self.planner is not None:
            plan = self.planner(task) or []
            return plan, {
                "task": task,
                "nodes": plan,
                "source": "injected_planner",
                "start": plan[0].get("name") if plan else None,
            }
        graph = self.planner_engine.plan_graph(task, self.world_state)
        return graph.to_plan(), graph.to_dict()

    @staticmethod
    def _recalled_skill(plan: List[Dict[str, Any]], graph: Dict[str, Any]) -> Optional[str]:
        source = str(graph.get("source") or "")
        if source.startswith("skill:"):
            return source.split(":", 1)[1]
        for step in plan:
            if step.get("_recalled_from"):
                return str(step["_recalled_from"])
            metadata = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
            if metadata.get("recalled_from"):
                return str(metadata["recalled_from"])
        return None

    def resume(self, task_id: str, dry_run: bool = False) -> Dict[str, Any]:
        checkpoint = self.task_store.load(task_id)
        if checkpoint is None:
            return {"success": False, "task_id": task_id, "error": f"task '{task_id}' was not found"}
        if checkpoint.status == "success" and not checkpoint.pending_states:
            return {
                "success": True,
                "task_id": task_id,
                "task": checkpoint.task,
                "result": "SUCCESS",
                "resumed": False,
                "message": "task is already complete",
                "checkpoint": checkpoint.to_dict(),
            }
        next_state = self.task_store.next_state(checkpoint)
        if next_state is None:
            return {
                "success": False,
                "task_id": task_id,
                "error": "task has no pending state to resume",
                "checkpoint": checkpoint.to_dict(),
            }
        self.world_state = WorldState.from_dict(checkpoint.world_state)
        self.planner_engine.world_state = self.world_state
        return self.run(
            checkpoint.task,
            task_id=checkpoint.task_id,
            dry_run=dry_run,
            plan=checkpoint.plan,
            graph=checkpoint.graph,
            resume=True,
            start_state=next_state,
        )

    # -- main loop ------------------------------------------------------
    def run(
        self,
        task: str,
        task_id: Optional[str] = None,
        dry_run: bool = False,
        plan: Optional[List[Dict[str, Any]]] = None,
        graph: Optional[Dict[str, Any]] = None,
        resume: bool = False,
        start_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        if dry_run:
            from .keyboard import DryRunKeyboard
            from .mouse import DryRunMouse
            from .window_manager import DryRunWindowBackend

            self.controller.mouse = DryRunMouse()
            self.controller.keyboard = DryRunKeyboard()
            self.controller.windows = DryRunWindowBackend()

        started_monotonic = time.monotonic()
        task_id = task_id or f"{datetime.now().strftime('%Y-%m-%d')}-{_slug(task)}"
        artifacts = TaskArtifacts(task_id, root=str(self.policy.root))
        task_dir = artifacts.directory

        if plan is None:
            self.world_state.reset(task)
            if self.analyzer is not None and self.analyzer.vision_model is not None:
                initial_frame = self.recorder.capture_now(store=False)
                if initial_frame is not None:
                    self.world_state.update(
                        self.analyzer.observe_world(initial_frame), source="initial_vision"
                    )
            plan, graph = self._make_plan(task)
        else:
            plan = list(plan)
            graph = dict(graph or {"task": task, "nodes": plan, "source": "persisted"})
            self.world_state.begin_task(task, "RESUMING" if resume else "PLANNING")

        checkpoint = self.task_store.initialize(
            task_id,
            task,
            plan,
            graph=graph,
            world_state=self.world_state,
            resume=resume,
        )
        start_state = start_state or (self.task_store.next_state(checkpoint) if resume else None)
        state_names = [str(step.get("name") or f"STATE_{index}") for index, step in enumerate(plan)]
        source = str((graph or {}).get("source") or "planner")
        # Register with task control system for pause/resume/cancel
        task_control = get_task_control()
        task_control.register_task(task_id, task, initial_state=state_names[0] if state_names else "")
        if task_control.is_emergency_stop_active():
            return {"success": False, "task_id": task_id, "task": task,
                    "error": f"emergency stop: {task_control._emergency_stop_reason}",
                    "result": "EMERGENCY_STOP"}

        publish("task_started", {
            "task_id": task_id,
            "task": task,
            "resume": bool(resume),
            "states": state_names,
            "source": source,
        })
        publish("plan_created", {
            "task_id": task_id,
            "task": task,
            "source": source,
            "start": (graph or {}).get("start") or (state_names[0] if state_names else None),
            "states": state_names,
            "nodes": len(state_names),
            "resume": bool(resume),
        })
        publish("checkpoint_saved", {
            "task_id": task_id,
            "task": task,
            "state": checkpoint.current_state,
            "status": checkpoint.status,
            "path": str(self.task_store.state_path(task_id)),
            "reason": "task_initialized" if not resume else "task_resumed",
        })

        generation = checkpoint.resume_count if resume else 0
        recording_name = "recording.mp4" if generation == 0 else f"recording-resume-{generation}.mp4"
        recording_path = task_dir / recording_name
        ffmpeg = VideoWriter.available()["available"]
        started = self.recorder.start(output_path=str(recording_path)) if ffmpeg else self.recorder.start()
        if not started.get("success"):
            started = self.recorder.start()

        # Preserve prior timeline/evidence across resume generations.
        previous_timeline = _read_json(task_dir / "timeline.json", {}) if resume else {}
        previous_actions = _read_json(task_dir / "actions.json", []) if resume else []
        previous_verifications = _read_json(task_dir / "verification.json", []) if resume else []
        previous_repairs_payload = _read_json(task_dir / "repairs.json", {}) if resume else {}
        base_offset = max(
            [float(item.get("offset", 0.0)) for item in previous_timeline.get("events", []) if isinstance(item, dict)]
            or [0.0]
        )
        timeline = Timeline(task=task, recording=str(recording_path) if ffmpeg else None,
                            started=previous_timeline.get("started") if resume else None)
        for event in previous_timeline.get("events", []):
            if isinstance(event, dict):
                timeline.add(
                    event.get("offset", 0.0), event.get("type", "event"), event.get("description", ""),
                    event.get("confidence", 0.0), event.get("timestamp"), event.get("evidence", {}),
                )
        timeline.add(base_offset, "task_resume" if resume else "task_start",
                     f"{'Resume' if resume else 'Task'}: {task}", 1.0, _now(),
                     {"generation": generation, "start_state": start_state})

        if dry_run:
            print("\n[SIMULATION MODE] Plan for:", task)
            for index, step in enumerate(plan, 1):
                action = step.get("action") or {}
                target = action.get("target") or action.get("name") or action.get("text") or action.get("condition") or ""
                print(f"  {index}. {step.get('name', 'STEP')}: {action.get('kind', 'act')} {target}")
                if step.get("expected"):
                    print(f"     Expected: {step['expected']}")
            print("[SIMULATION MODE] No real actions will be performed.\n")

        recalled_skill = self._recalled_skill(plan, graph or {})
        if recalled_skill:
            publish("skill_recalled", {
                "task_id": task_id,
                "task": task,
                "skill": recalled_skill,
                "source": source,
            })
            skill = self.skills.get_skill(recalled_skill)
            if skill and hasattr(self.repair_engine, "set_known_repairs"):
                self.repair_engine.set_known_repairs(skill.repairs)

        states = VisualStateMachine.plan_to_states(plan)

        def checkpoint_event(event: Dict[str, Any]) -> None:
            saved = self.task_store.checkpoint_event(checkpoint, event, self.world_state)
            machine_event(event, task_id=task_id, task=task, emit_lifecycle_starts=False)
            publish("checkpoint_saved", {
                "task_id": task_id,
                "task": task,
                "state": saved.current_state,
                "status": saved.status,
                "phase": event.get("phase"),
                "path": str(self.task_store.state_path(task_id)),
            })
            try:
                world = self.world_state.to_dict(include_history=False)
                publish("world_changed", {
                    "task_id": task_id,
                    "task": task,
                    "world": {
                        "application": world.get("active_application"),
                        "window": world.get("active_window"),
                        "task_state": world.get("task_state"),
                        "confidence": world.get("confidence"),
                        "visible_targets": list(world.get("visible_targets") or []),
                        "dialogs": list(world.get("dialogs") or []),
                    },
                })
            except Exception:  # noqa: BLE001
                pass

        def live_telemetry(event_type: str, data: Dict[str, Any]) -> None:
            payload = {"task_id": task_id, "task": task, **dict(data or {})}
            spec = payload.get("action_spec")
            if isinstance(spec, dict):
                kind = str(spec.get("kind") or spec.get("action") or "")
                target = str(spec.get("target") or spec.get("name") or spec.get("text") or spec.get("key") or "")
                payload["action"] = f"{kind} {target}".strip()
            verification = payload.pop("verification", None)
            if isinstance(verification, dict):
                payload.update({
                    "ok": bool(verification.get("ok")),
                    "matched": bool(verification.get("matched", verification.get("ok"))),
                    "confidence": verification.get("confidence", 0.0),
                    "detail": verification.get("detail") or verification.get("error") or "",
                })
            publish(event_type, payload)

        machine = VisualStateMachine(
            controller=self.controller,
            recorder=self.recorder,
            wait_until=(lambda condition, timeout: {"matched": True, "success": True,
                                                     "detail": "simulation condition"}) if dry_run else self.wait_until,
            execute=lambda spec: dispatch_action(self.controller, spec),
            verify=(lambda before, after, expected: {"ok": True, "matched": True,
                                                     "detail": "simulation verification",
                                                     "confidence": 1.0}) if dry_run else self._verify,
            repair=self.repair_engine.create_plan,
            max_retries=self.max_retries,
            world_state=self.world_state,
            on_event=checkpoint_event,
            on_telemetry=live_telemetry,
        )
        try:
            report = machine.run(states, timeout_per_state=60.0, start_state=start_state, task_id=task_id)

            # Adaptive replanning: if the state machine failed but not from
            # user cancellation or emergency stop, try replanning the branch.
            if not report.get("success"):
                failure = report.get("failure") or {}
                failure_category = str(failure.get("category") or "")
                is_user_cancel = (
                    task_control.is_cancel_requested(task_id)
                    or failure_category == "cancelled"
                )
                is_emergency = (
                    task_control.is_emergency_stop_active()
                    or failure_category == "emergency_stop"
                )
                if not is_user_cancel and not is_emergency:
                    replan_count = 0
                    max_replans = getattr(self.replanner, "max_replans", 3)
                    while replan_count < max_replans and not report.get("success"):
                        replan_count += 1
                        if not self.replanner.can_replan(replan_count, max_replans):
                            break

                        publish("replan_started", {
                            "task_id": task_id,
                            "task": task,
                            "attempt": replan_count,
                            "reason": failure.get("reason", report.get("error", "unknown")),
                            "failed_state": report.get("final_state") or failure.get("state", ""),
                        })

                        # Build context for replanning
                        future_plan = list(plan) if plan else []
                        past_plan = []
                        current_name = report.get("final_state") or failure.get("state")
                        if current_name and plan:
                            past_plan = []
                            future_plan = []
                            found = False
                            for p in plan:
                                if found:
                                    future_plan.append(p)
                                elif p.get("name") == current_name:
                                    found = True
                                    future_plan.append(p)
                                else:
                                    past_plan.append(p)

                        replan_context = ReplanContext(
                            original_task=task,
                            current_state=report.get("final_state") or "unknown",
                            expected_state=plan[0].get("expected", "") if plan else "",
                            observed_state=self.world_state.to_dict(include_history=False),
                            world_state=self.world_state,
                            plan_so_far=past_plan or list(plan if plan else []),
                            remaining_plan=future_plan,
                            failure_reason=failure.get("reason", report.get("error", "")),
                        )
                        new_graph, deltas = self.replanner.replan(replan_context)

                        if new_graph and deltas:
                            publish("plan_updated", {
                                "task_id": task_id,
                                "task": task,
                                "deltas": [d.to_dict() for d in deltas],
                                "replan_attempt": replan_count,
                            })
                            # Run again with the updated plan
                            new_plan = new_graph.to_plan()
                            new_states = VisualStateMachine.plan_to_states(new_plan)
                            report = machine.run(new_states, timeout_per_state=60.0,
                                                 start_state=None, task_id=task_id)
                        else:
                            break

        except (KeyboardInterrupt, SystemExit):
            self.recorder.stop()
            self.task_store.mark_interrupted(task_id, "task interrupted by user")
            publish("task_interrupted", {"task_id": task_id, "task": task, "reason": "task interrupted by user"})
            raise
        except Exception as exc:  # noqa: BLE001
            report = {
                "success": False,
                "states_visited": [],
                "final_state": start_state,
                "error": f"computer task crashed: {exc}",
                "failure": {"state": start_state, "category": "agent_exception", "reason": str(exc)},
            }
            self.task_store.mark_interrupted(task_id, report["error"])
            publish("task_interrupted", {"task_id": task_id, "task": task, "reason": report["error"]})

        # Collect structured evidence from the current generation's trace.
        actions: List[Dict[str, Any]] = list(previous_actions) if isinstance(previous_actions, list) else []
        verifications: List[Dict[str, Any]] = list(previous_verifications) if isinstance(previous_verifications, list) else []
        diagnoses: List[Dict[str, Any]] = list(previous_repairs_payload.get("diagnoses", [])) if isinstance(previous_repairs_payload, dict) else []
        repairs: List[Dict[str, Any]] = list(previous_repairs_payload.get("repairs", [])) if isinstance(previous_repairs_payload, dict) else []
        retries = 0
        for visited in report.get("states_visited", []):
            phase = visited.get("phase", "")
            action = visited.get("action")
            verification = visited.get("verification")
            if phase == "diagnose":
                diagnoses.append({
                    "state": visited.get("state"),
                    "attempt": visited.get("attempt"),
                    "failure_reason": visited.get("failure_reason"),
                    "diagnosis": visited.get("diagnosis", {}),
                    "repair_plan": visited.get("repair_plan", {}),
                    "repair_error": visited.get("repair_error"),
                })
            if phase == "original_action" and int(visited.get("attempt", 1) or 1) > 1:
                retries += 1

            step_ts = action.get("ts") if isinstance(action, dict) else None
            step_offset = base_offset
            if step_ts:
                try:
                    event_dt = datetime.fromisoformat(step_ts)
                    current_started = datetime.fromisoformat(started.get("started") or _now())
                    step_offset += max(0.0, (event_dt - current_started).total_seconds())
                except (ValueError, TypeError):
                    step_offset = base_offset

            if isinstance(action, dict) and action:
                action_record = {
                    **action,
                    "state": visited.get("state"),
                    "phase": phase,
                    "attempt": visited.get("attempt"),
                    "repair_for": visited.get("repair_for"),
                    "repair_state": visited.get("repair_state"),
                    "outcome": visited.get("outcome"),
                    "generation": generation,
                }
                actions.append(action_record)
                if phase == "repair":
                    repairs.append({
                        "state": visited.get("repair_state"),
                        "repair_for": visited.get("repair_for"),
                        "repair_plan_id": visited.get("repair_plan_id"),
                        "failure": next((item.get("failure_reason") for item in reversed(diagnoses)
                                         if item.get("state") == visited.get("state")), None),
                        "action": action_record,
                        "verification": verification,
                        "success": visited.get("outcome") == "success",
                        "outcome": visited.get("outcome"),
                        "failure_reason": visited.get("failure_reason"),
                    })
                timeline.add(
                    step_offset,
                    f"repair:{action.get('action', 'action')}" if phase == "repair" else action.get("action", "action"),
                    action.get("description") or str(action.get("action", "action")),
                    action.get("confidence", 0.0),
                    action.get("ts"),
                    {"action": action.get("action"), "args": action.get("args", {}),
                     "state": visited.get("state"), "phase": phase, "generation": generation},
                )
            if isinstance(verification, dict) and verification:
                verification_record = {
                    **verification,
                    "state": visited.get("state"),
                    "phase": phase,
                    "attempt": visited.get("attempt"),
                    "repair_state": visited.get("repair_state"),
                    "generation": generation,
                }
                verifications.append(verification_record)
                timeline.add(
                    step_offset,
                    "verify_repair" if phase == "repair" else "verify",
                    verification.get("detail") or ("PASS" if verification.get("ok") else "FAIL"),
                    verification.get("confidence", 0.0),
                    step_ts,
                    {"ok": bool(verification.get("ok")), "state": visited.get("state"),
                     "phase": phase, "generation": generation},
                )

        stopped = self.recorder.stop()
        recording = (stopped.get("video") or {}).get("path") if ffmpeg else None
        if recording is None and ffmpeg and recording_path.exists():
            recording = str(recording_path)

        success = bool(report.get("success"))
        duration = time.monotonic() - started_monotonic
        self.world_state.finish_task(success)
        timeline.add(base_offset + duration, "task_end", "SUCCESS" if success else "FAILURE",
                     1.0 if success else 0.0, _now(), {"generation": generation})

        visual_states = [
            str(item.get("expected")) for item in plan if isinstance(item, dict) and item.get("expected")
        ]
        if recalled_skill:
            self.skills.record_run(
                recalled_skill,
                success=success,
                error=report.get("error"),
                duration=duration,
                repairs=repairs,
                visual_states=visual_states,
                evidence={"runs": {"task_id": task_id, "recording": recording, "success": success}},
            )

        result_payload = {
            "success": success,
            "task": task,
            "task_id": task_id,
            "duration": duration,
            "actions": len(actions),
            "retries": retries,
            "repair_steps": len(repairs),
            "diagnoses": diagnoses,
            "failure": report.get("failure"),
            "result": "SUCCESS" if success else "FAILURE",
            "resumed": resume,
            "generation": generation,
        }
        bundle = artifacts.write(
            timeline=timeline,
            events=[],
            actions=actions,
            result=result_payload,
            recording_path=recording,
        )

        verification_path = task_dir / "verification.json"
        verification_path.write_text(json.dumps(verifications, indent=2, default=str), encoding="utf-8")
        repairs_path = task_dir / "repairs.json"
        repairs_path.write_text(json.dumps({"diagnoses": diagnoses, "repairs": repairs}, indent=2, default=str), encoding="utf-8")
        summary_path = task_dir / "summary.md"
        summary_path.write_text(
            self._summary(task, success, duration, actions, verifications, retries, repairs,
                          report.get("failure"), timeline, resume=resume),
            encoding="utf-8",
        )
        self.policy.secure(verification_path)
        self.policy.secure(repairs_path)
        self.policy.secure(summary_path)

        # Promote a new successful graph. Recalled skills were updated above.
        skill_result = None
        if success and self.learn_skills and not recalled_skill:
            procedure = []
            for step in plan:
                if not isinstance(step, dict) or not isinstance(step.get("action"), dict):
                    continue
                procedure.append({
                    "name": step.get("name"),
                    "goal": step.get("goal", ""),
                    "precondition": step.get("precondition", ""),
                    "action": step["action"],
                    "expected": step.get("expected", ""),
                    "on_success": step.get("on_success"),
                    "on_failure": step.get("on_failure"),
                })
            skill_result = self.skills.save_skill(
                task,
                procedure,
                evidence={"recording": recording, "task_id": task_id,
                          "runs": [{"task_id": task_id, "success": True}]},
                duration=duration,
                repairs=repairs,
                visual_states=visual_states,
            )

        final_result = {
            **result_payload,
            "duration_text": _duration_text(duration),
            "actions": actions,
            "verifications": verifications,
            "diagnoses": diagnoses,
            "repairs": repairs,
            "states_visited": report.get("states_visited", []),
            "world_state": self.world_state.to_dict(),
            "timeline": timeline.to_dict(),
            "timeline_text": timeline.render_text(),
            "recording": recording,
            "artifacts": bundle,
            "state_path": str(self.task_store.state_path(task_id)),
            "verification_path": str(verification_path),
            "repairs_path": str(repairs_path),
            "summary_path": str(summary_path),
            "skill": skill_result,
            "error": report.get("error"),
        }
        # Notify task control of completion
        if success:
            task_control.complete_task(task_id, success=True)
        else:
            task_control.complete_task(task_id, success=False)
        task_control.unregister_task(task_id)

        completed_checkpoint = self.task_store.complete(checkpoint, success, final_result, self.world_state, recording)
        publish("checkpoint_saved", {
            "task_id": task_id,
            "task": task,
            "state": completed_checkpoint.current_state,
            "status": completed_checkpoint.status,
            "path": str(self.task_store.state_path(task_id)),
            "reason": "task_completed" if success else "task_failed",
        })
        publish("task_completed" if success else "task_failed", {
            "task_id": task_id,
            "task": task,
            "success": success,
            "result": "SUCCESS" if success else "FAILURE",
            "duration": round(duration, 2),
            "actions": len(actions),
            "retries": retries,
            "repairs": len(repairs),
            "verifications": len(verifications),
            "error": report.get("error"),
            "recording": recording,
        })
        final_result["checkpoint"] = self.task_store.load(task_id).to_dict()
        return final_result

    # -- verification ---------------------------------------------------
    def _verify(self, before: Any, after: Any, expected: str) -> Dict[str, Any]:
        if self.analyzer is not None and self.analyzer.vision_model is not None:
            result = self.analyzer.evaluate_transition(before, after, expected or "the action had its intended effect")
            result["ok"] = bool(result.get("matched"))
            return result
        return self.verifier.verify(before, after, expected)

    @staticmethod
    def _summary(
        task: str,
        success: bool,
        duration: float,
        actions: List[Dict[str, Any]],
        verifications: List[Dict[str, Any]],
        retries: int,
        repairs: List[Dict[str, Any]],
        failure: Optional[Dict[str, Any]],
        timeline: Timeline,
        resume: bool = False,
    ) -> str:
        lines = [
            f"# Task: {task}",
            "",
            f"Duration: {_duration_text(duration)}",
            f"Actions: {len(actions)}",
            f"Visual verifications: {len(verifications)}",
            f"Repair steps: {len(repairs)}",
            f"Retries: {retries}",
            f"Resumed: {'yes' if resume else 'no'}",
            "",
            f"Result: {'SUCCESS' if success else 'FAILURE'}",
        ]
        if failure:
            lines.extend([
                f"Failure category: {failure.get('category', 'unknown')}",
                f"Failure reason: {failure.get('reason', 'unknown')}",
            ])
        lines.extend(["", "Timeline:"])
        for event in timeline.events:
            minutes, seconds = divmod(max(0, int(event.offset)), 60)
            lines.append(f"{minutes:02d}:{seconds:02d} {event.description}")
        return "\n".join(lines) + "\n"
