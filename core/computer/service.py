"""Small background service used by ``hermus screen record`` commands.

A CLI process cannot keep an in-memory recorder alive after it exits.  This
controller launches one detached local process, persists only status/paths to a
private state file, and lets later CLI invocations stop and save the session.
The gateway continues to use its own in-process recorder.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .event_detector import StreamingEventDetector
from .permissions import RecordingPolicy, recording_policy
from .recorder import ImageGrabSource, ScreenRecorder
from .timeline import TaskArtifacts, Timeline
from .video_writer import VideoWriter


class ScreenRecordingService:
    def __init__(self, policy: Optional[RecordingPolicy] = None):
        self.policy = policy or recording_policy
        self.root = self.policy.root
        self.state_path = self.root / ".screen-recorder.json"
        self.repo_root = Path(__file__).resolve().parents[2]

    def _read_state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"status": "not_started", "running": False}

    def _write_state(self, state: Dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        temporary.replace(self.state_path)
        try:
            self.root.chmod(0o700)
            self.state_path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _pid_alive(pid: Any) -> bool:
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def status(self) -> Dict[str, Any]:
        state = self._read_state()
        alive = self._pid_alive(state.get("pid"))
        state["running"] = bool(alive and state.get("status") in {"starting", "recording", "stopping"})
        if state.get("status") in {"starting", "recording", "stopping"} and not alive:
            state["status"] = "error"
            state["error"] = state.get("error") or "recorder service exited unexpectedly"
        state["state_file"] = str(self.state_path)
        return state

    def start(self, fps: float = 10.0, max_seconds: float = 30.0, container: str = "mp4") -> Dict[str, Any]:
        current = self.status()
        if current.get("running"):
            return {"success": False, "error": "screen recorder service already running", **current}
        valid = self.policy.validate_settings(fps, max_seconds)
        if not valid.get("ok"):
            return {"success": False, "error": valid["error"]}
        container = container.lower().lstrip(".")
        if container not in {"mp4", "webm"}:
            return {"success": False, "error": "format must be mp4 or webm"}
        available = VideoWriter.available()
        if not available["available"]:
            return {"success": False, "error": "FFmpeg unavailable", **available}

        session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        session_dir = self.root / ".sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        try:
            session_dir.chmod(0o700)
        except OSError:
            pass
        output = session_dir / f"recording.{container}"
        state = {
            "success": True,
            "status": "starting",
            "running": True,
            "session_id": session_id,
            "session_dir": str(session_dir),
            "output_path": str(output),
            "fps": float(fps),
            "max_seconds": float(max_seconds),
            "started": datetime.now().astimezone().isoformat(),
        }
        self._write_state(state)
        command = [
            sys.executable,
            "-m",
            "core.computer.service",
            "run",
            "--session-id",
            session_id,
            "--output",
            str(output),
            "--fps",
            str(fps),
            "--max-seconds",
            str(max_seconds),
        ]
        log_path = session_dir / "service.log"
        log = open(log_path, "ab")  # kept for detached-process diagnostics
        self.policy.secure(log_path)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.repo_root),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
        except Exception as exc:
            log.close()
            state.update({"success": False, "status": "error", "running": False, "error": str(exc)})
            self._write_state(state)
            return state
        finally:
            try:
                log.close()
            except Exception:
                pass
        state["pid"] = process.pid
        self._write_state(state)
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            latest = self._read_state()
            if latest.get("session_id") == session_id and latest.get("status") in {"recording", "error"}:
                return latest
            if process.poll() is not None:
                break
            time.sleep(0.1)
        latest = self.status()
        if latest.get("status") == "starting":
            latest.update({"success": False, "error": "recorder service did not become ready"})
        return latest

    def stop(self, timeout: float = 20.0) -> Dict[str, Any]:
        state = self.status()
        if not state.get("running"):
            if state.get("status") == "stopped":
                return state
            return {"success": False, "error": "screen recorder service is not running", **state}
        pid = int(state["pid"])
        state["status"] = "stopping"
        self._write_state(state)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            return {"success": False, "error": f"could not stop recorder service: {exc}"}
        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() < deadline:
            latest = self._read_state()
            if latest.get("status") in {"stopped", "error"}:
                latest["running"] = False
                return latest
            time.sleep(0.1)
        return {"success": False, "error": "recorder service did not stop before timeout", **self.status()}

    def save(self, target: str) -> Dict[str, Any]:
        state = self.status()
        if state.get("running"):
            return {"success": False, "error": "stop the recorder before saving"}
        source = Path(state.get("output_path") or "").expanduser()
        if not source.exists() or source.stat().st_size == 0:
            return {"success": False, "error": "no finalized recording is available"}
        requested = Path(target)
        session_dir = Path(state.get("session_dir") or source.parent)

        if requested.suffix.lower() in {".mp4", ".webm"}:
            try:
                destination = self.policy.output_path(target)
            except (ValueError, PermissionError) as exc:
                return {"success": False, "error": str(exc)}
            if destination.suffix.lower() != source.suffix.lower():
                return {"success": False, "error": "changing MP4/WebM container requires re-encoding; use the original extension"}
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            self.policy.secure(destination)
            sidecars = {}
            for name in ("timeline", "events", "actions", "result"):
                side_source = session_dir / f"{name}.json"
                if side_source.exists():
                    side_target = destination.parent / f"{destination.stem}.{name}.json"
                    shutil.copy2(side_source, side_target)
                    self.policy.secure(side_target)
                    sidecars[name] = str(side_target)
            return {"success": True, "recording": str(destination), "sidecars": sidecars}

        try:
            task_id = self.policy.task_id(target)
            timeline = json.loads((session_dir / "timeline.json").read_text(encoding="utf-8")) if (session_dir / "timeline.json").exists() else None
            events = json.loads((session_dir / "events.json").read_text(encoding="utf-8")) if (session_dir / "events.json").exists() else []
            actions = json.loads((session_dir / "actions.json").read_text(encoding="utf-8")) if (session_dir / "actions.json").exists() else []
            result = json.loads((session_dir / "result.json").read_text(encoding="utf-8")) if (session_dir / "result.json").exists() else state
            return TaskArtifacts(task_id, root=str(self.root)).write(
                timeline=timeline,
                events=events,
                actions=actions,
                result=result,
                recording_path=str(source),
            )
        except Exception as exc:
            return {"success": False, "error": f"could not save task bundle: {exc}"}

    def run_daemon(self, session_id: str, output: str, fps: float, max_seconds: float) -> int:
        stop_event = threading.Event()

        def request_stop(_signum: int, _frame: Any) -> None:
            stop_event.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        recorder = ScreenRecorder(source=ImageGrabSource(), fps=fps, max_seconds=max_seconds)
        started = recorder.start(output_path=output)
        state = self._read_state()
        state.update({"pid": os.getpid(), "session_id": session_id})
        if not started.get("success"):
            state.update({"success": False, "status": "error", "running": False, "error": started.get("error")})
            self._write_state(state)
            return 1
        state.update({"success": True, "status": "recording", "running": True})
        self._write_state(state)
        # Inspect each new compressed frame while retaining only event metadata.
        # Unlike the rolling frame deque, this timeline covers a recording of
        # any duration without retaining every screenshot.
        stream_events = StreamingEventDetector()
        inspect_interval = max(0.02, min(0.1, 1.0 / max(0.1, fps)))
        next_status = time.monotonic()
        while not stop_event.wait(inspect_interval):
            stream_events.observe(recorder.latest())
            if time.monotonic() >= next_status:
                latest = recorder.status()
                state.update(latest)
                state.update({"success": True, "status": "recording", "running": True, "pid": os.getpid(), "session_id": session_id})
                self._write_state(state)
                next_status = time.monotonic() + 0.5

        stream_events.observe(recorder.latest())
        stopped = recorder.stop()
        recording_path = (stopped.get("video") or {}).get("path") or output
        timeline = Timeline("Screen recording", recording_path, started=state.get("started"))
        timeline.add(0.0, "initial_state", "Recording started", 1.0, state.get("started"), {"recording": recording_path, "recording_at": 0.0})
        detected = stream_events.events()
        for event in detected:
            timeline.add(
                event.get("offset") or 0.0,
                "visual_event",
                f"Screen changed (score {event.get('change_score', 0.0):.3f})",
                0.0,
                event.get("ts"),
                {"recording": recording_path, "recording_at": event.get("offset"), "sequence": event.get("sequence"), "change_score": event.get("change_score")},
            )
        analysis = {"timeline": timeline.to_dict(), "events": detected}
        session_dir = Path(state.get("session_dir") or Path(output).parent)
        session_dir.mkdir(parents=True, exist_ok=True)
        for name, value in (
            ("timeline.json", analysis.get("timeline", {})),
            ("events.json", analysis.get("events", [])),
            ("actions.json", recorder.markers()),
            ("result.json", stopped),
        ):
            path = session_dir / name
            path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
            self.policy.secure(path)
        state.update(stopped)
        state.update({
            "success": bool(stopped.get("success")),
            "status": "stopped" if stopped.get("success") else "error",
            "running": False,
            "pid": os.getpid(),
            "output_path": recording_path,
            "timeline_path": str(session_dir / "timeline.json"),
            "finished": datetime.now().astimezone().isoformat(),
        })
        self._write_state(state)
        return 0 if stopped.get("success") else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--session-id", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--fps", type=float, required=True)
    run.add_argument("--max-seconds", type=float, required=True)
    args = parser.parse_args()
    if args.command == "run":
        raise SystemExit(ScreenRecordingService().run_daemon(args.session_id, args.output, args.fps, args.max_seconds))


if __name__ == "__main__":
    main()
