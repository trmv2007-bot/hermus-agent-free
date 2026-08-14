"""Hermus Computer Agent v1: recording, understanding and verification."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from PIL import Image

from core.computer.service import ScreenRecordingService
from core.computer import (
    ActionVerificationManager,
    CallableSource,
    EventDetector,
    RecordingPolicy,
    ScreenRecorder,
    ScreenVerifier,
    ScreenWatcher,
    StreamingEventDetector,
    TaskArtifacts,
    VideoAnalyzer,
    VideoWriter,
    encode_image,
)


def _record(color: str, sequence: int, offset: float):
    image = Image.new("RGB", (48, 32), color)
    data = encode_image(image)
    return {
        "ts": f"2026-01-01T00:00:0{sequence}+00:00",
        "captured_at": time.time() + offset,
        "offset": offset,
        "sequence": sequence,
        "encoding": "jpeg",
        "bytes": len(data),
        "data": data,
    }


def test_recorder_keeps_compressed_bounded_ram():
    recorder = ScreenRecorder(
        source=CallableSource(lambda: Image.new("RGB", (128, 72), "navy")),
        fps=40,
        max_seconds=2,
        max_buffer_mb=0.002,
    )
    assert recorder.start()["success"]
    time.sleep(0.18)
    recorder.stop()
    frames = recorder.all_frames()
    assert frames
    assert all("data" in frame and "image" not in frame for frame in frames)
    assert all(isinstance(frame["data"], bytes) for frame in frames)
    status = recorder.status()
    # The recorder keeps at least the newest frame if a single frame exceeds
    # the configured guard, otherwise it remains under that hard guard.
    assert status["buffer_bytes"] <= recorder.max_buffer_bytes or len(frames) == 1


def test_event_detector_and_semantic_timeline():
    frames = [
        _record("black", 1, 0.0),
        _record("black", 2, 0.2),
        _record("white", 3, 1.0),
        _record("white", 4, 1.2),
        _record("red", 5, 2.0),
    ]
    detector = EventDetector(threshold=0.02, debounce_seconds=0.3)
    events = detector.detect(frames)
    assert len(events) == 2

    replies = iter([
        "Desktop is visible",
        "A terminal opened",
        "An error dialog appeared",
    ])
    analyzer = VideoAnalyzer(
        vision_model=lambda _image, _prompt: {
            "description": next(replies),
            "confidence": 0.9,
        },
        event_detector=detector,
    )
    result = analyzer.analyze(frames, task="Install X", recording="task.mp4")
    descriptions = [event["description"] for event in result["timeline"]["events"]]
    assert descriptions == ["Desktop is visible", "A terminal opened", "An error dialog appeared"]
    assert result["semantic"] is True
    assert all("data" not in event for event in result["events"])
    assert result["timeline"]["events"][-1]["evidence"]["recording_at"] == 2.0


def test_streaming_detector_keeps_only_event_metadata():
    detector = StreamingEventDetector(threshold=0.02, debounce_seconds=0.3)
    for frame in [
        _record("black", 1, 0.0),
        _record("white", 2, 1.0),
        _record("red", 3, 2.0),
    ]:
        detector.observe(frame)
    events = detector.events()
    assert len(events) == 2
    assert all("data" not in event and "frame" not in event for event in events)


def test_real_mp4_roundtrip(tmp_path):
    if not VideoWriter.available()["available"]:
        pytest.skip("FFmpeg not installed")
    output = tmp_path / "recording.mp4"
    frames = [_record("black", 1, 0.0), _record("white", 2, 0.5), _record("red", 3, 1.0)]
    result = VideoWriter.write_frames(str(output), frames, fps=2)
    assert result["success"] and output.stat().st_size > 0

    extracted = VideoAnalyzer.frames_from_video(str(output), sample_fps=2, max_seconds=5)
    assert extracted["success"]
    assert extracted["frames"]
    assert all(isinstance(frame["data"], bytes) for frame in extracted["frames"])

    # User-facing video analysis streams the source and keeps only selected
    # transition pairs, rather than loading the full video into RAM.
    analyzed = VideoAnalyzer().analyze_video(
        str(output), sample_fps=2, max_seconds=5, max_events=1
    )
    assert analyzed["success"]
    assert analyzed["frames_analyzed"] <= 2
    assert analyzed["frames_total"] >= analyzed["frames_analyzed"]


def test_before_after_verification_returns_visual_memory():
    before, after = Image.new("RGB", (32, 32), "black"), Image.new("RGB", (32, 32), "white")
    verifier = ScreenVerifier(
        transition_model=lambda before_image, after_image, expected: {
            "matched": (
                before_image.getpixel((0, 0)) == (0, 0, 0)
                and after_image.getpixel((0, 0)) == (255, 255, 255)
                and expected == "Install button changed to Open"
            ),
            "detail": "Install changed to Open",
            "confidence": 0.97,
        }
    )
    result = verifier.verify_action(
        "clicked Install",
        before,
        after,
        "Install button changed to Open",
        recording="task.mp4",
        offset=18.4,
    )
    assert result["ok"] is True
    assert result["memory"] == {
        "action": "clicked Install",
        "visual_result": "Install changed to Open",
        "confidence": 0.97,
        "evidence": {"recording": "task.mp4", "offset": 18.4},
        "success": True,
    }


def test_action_manager_captures_exact_boundaries():
    class FakeRecorder:
        running = True

        def __init__(self):
            self.frames = iter([_record("black", 1, 1.0), _record("white", 2, 2.0)])
            self.marks = []

        def capture_now(self, store=True):
            return next(self.frames)

        def mark(self, label, kind="action", metadata=None):
            marker = {"label": label, "type": kind, "metadata": metadata or {}}
            self.marks.append(marker)
            return marker

        def status(self):
            return {"video": {"path": "recording.mp4"}}

    recorder = FakeRecorder()
    manager = ActionVerificationManager(recorder)
    before = manager.before("clicked Install", "button changed")
    assert before["success"] and manager.pending()
    after = manager.after(before["action_id"], ScreenVerifier())
    assert after["verification"]["ok"]
    assert [mark["type"] for mark in recorder.marks] == ["action_before", "action_after"]
    assert not manager.pending()


def test_screen_watcher_checks_only_new_changed_frames():
    class FakeRecorder:
        running = True

        def __init__(self):
            self.index = 0
            self.frames = [
                _record("black", 1, 0.0),
                _record("white", 2, 0.1),
                _record("white", 3, 0.2),
            ]

        def latest(self):
            frame = self.frames[min(self.index, 2)]
            self.index += 1
            return frame

    watcher = ScreenWatcher(
        FakeRecorder(),
        evaluator=lambda frame, _condition: {
            "matched": frame["sequence"] >= 2,
            "confidence": 0.99,
            "detail": "download complete",
        },
    )
    result = watcher.watch(
        "download completes", timeout=1, poll_interval=0.01, stable_matches=2
    )
    assert result["success"] and result["matched"]
    assert result["evidence"]["sequence"] == 3


def test_private_task_artifact_bundle(tmp_path):
    recording = tmp_path / "source.mp4"
    recording.write_bytes(b"video")
    bundle = TaskArtifacts("task 042", root=str(tmp_path / "recordings")).write(
        timeline={"events": [{"description": "SUCCESS"}]},
        events=[{"type": "screen_change"}],
        actions=[{"label": "clicked Install"}],
        result={"success": True},
        recording_path=str(recording),
    )
    directory = Path(bundle["directory"])
    assert (directory / "recording.mp4").read_bytes() == b"video"
    assert json.loads((directory / "actions.json").read_text())[0]["label"] == "clicked Install"
    assert Path(bundle["manifest"]).name == "manifest.json"
    assert Path(bundle["manifest"]).exists()


def test_detached_service_save_supports_video_and_task_bundle(tmp_path):
    root = tmp_path / "recordings"
    policy = RecordingPolicy(str(root))
    service = ScreenRecordingService(policy)
    session = root / ".sessions" / "session-1"
    session.mkdir(parents=True)
    recording = session / "recording.mp4"
    recording.write_bytes(b"video")
    for name, value in {
        "timeline.json": {"events": []},
        "events.json": [],
        "actions.json": [],
        "result.json": {"success": True},
    }.items():
        (session / name).write_text(json.dumps(value), encoding="utf-8")
    service._write_state({
        "success": True,
        "status": "stopped",
        "running": False,
        "session_dir": str(session),
        "output_path": str(recording),
    })

    video = service.save("final.mp4")
    assert video["success"]
    assert Path(video["recording"]).read_bytes() == b"video"
    assert Path(video["sidecars"]["timeline"]).exists()

    bundle = service.save("task_99")
    assert bundle["success"]
    assert Path(bundle["recording"]).name == "recording.mp4"
    assert Path(bundle["manifest"]).exists()


def test_recording_policy_rejects_escape(tmp_path):
    policy = RecordingPolicy(str(tmp_path / "recordings"))
    assert policy.output_path("task.mp4").parent == (tmp_path / "recordings").resolve()
    with pytest.raises(PermissionError):
        policy.output_path("../private.mp4")
    with pytest.raises(ValueError):
        policy.output_path("not-a-video.txt")
