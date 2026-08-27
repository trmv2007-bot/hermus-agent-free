"""Semantic understanding for selected recording events.

The analyzer first uses :class:`EventDetector` to reduce a recording to a few
important transitions.  It then sends a baseline image and before/after
composites to an injected vision model (or Hermus's local Ollama vision tool)
and returns an agent-readable :class:`Timeline`.
"""
from __future__ import annotations

import heapq
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable

from .event_detector import EventDetector
from .frame_sampler import _image_diff
from .recorder import decode_frame, encode_image
from .timeline import Timeline
from .video_writer import VideoWriter


class OllamaVisionModel:
    """Adapter around the existing free local ``tools.vision`` integration."""

    def __init__(self, model: str = "llava:7b"):
        self.model = model

    def available(self) -> dict[str, Any]:
        from tools.vision import vision_available_models

        status = vision_available_models()
        models = status.get("vision_models") or []
        return {
            "available": bool(models),
            "models": models,
            "error": status.get("error"),
            "suggestion": status.get("suggestion"),
        }

    def __call__(self, image: Any, prompt: str) -> dict[str, Any]:
        from tools.vision import vision_analyze

        with tempfile.NamedTemporaryFile(suffix=".jpg") as temporary:
            data = encode_image(image, quality=85)
            if not data:
                return {"success": False, "error": "could not encode selected frame"}
            temporary.write(data)
            temporary.flush()
            return vision_analyze(temporary.name, prompt=prompt, model=self.model)


class VideoAnalyzer:
    def __init__(
        self,
        vision_model: Optional[Callable[[Any, str], dict[str, Any]]] = None,
        event_detector: Optional[EventDetector] = None,
    ):
        self.vision_model = vision_model
        self.event_detector = event_detector or EventDetector()

    @classmethod
    def with_ollama(cls, model: str = "llava:7b", **kwargs: Any) -> "VideoAnalyzer":
        return cls(vision_model=OllamaVisionModel(model), **kwargs)

    @staticmethod
    def _composite(before: Any, after: Any) -> Any:
        left, right = decode_frame(before), decode_frame(after)
        if left is None:
            return right
        if right is None:
            return left
        try:
            from PIL import Image, ImageDraw

            left = left.convert("RGB")
            right = right.convert("RGB")
            height = max(left.height, right.height)
            width = left.width + right.width
            canvas = Image.new("RGB", (width, height + 28), "white")
            canvas.paste(left, (0, 28))
            canvas.paste(right, (left.width, 28))
            draw = ImageDraw.Draw(canvas)
            draw.text((8, 7), "BEFORE", fill="black")
            draw.text((left.width + 8, 7), "AFTER", fill="black")
            return canvas
        except Exception:
            return right

    def _call_vision(self, image: Any, prompt: str) -> dict[str, Any]:
        if self.vision_model is None:
            return {"success": False, "error": "no vision model configured"}
        try:
            response = self.vision_model(image, prompt)
        except Exception as exc:
            return {"success": False, "error": f"vision model error: {exc}"}
        if isinstance(response, str):
            return {"success": True, "description": response, "confidence": 0.6}
        if not isinstance(response, dict):
            return {"success": False, "error": "vision model returned an unsupported result"}
        text = (
            response.get("description")
            or response.get("detail")
            or response.get("response")
            or response.get("result")
            or ""
        )
        return {
            **response,
            "success": response.get("success", True),
            "description": str(text).strip(),
            "confidence": float(response.get("confidence", 0.65 if text else 0.0)),
        }

    @staticmethod
    def _evidence(event: dict[str, Any], recording: Optional[str] = None) -> dict[str, Any]:
        evidence = {
            "sequence": event.get("sequence"),
            "timestamp": event.get("ts"),
            "offset": event.get("offset"),
            "change_score": event.get("change_score"),
        }
        if recording:
            evidence["recording"] = recording
            evidence["recording_at"] = event.get("offset")
        return evidence

    def _analyze_detected(
        self,
        baseline: dict[str, Any],
        detected: list[dict[str, Any]],
        task: str,
        recording: Optional[str],
        frames_total: int,
    ) -> dict[str, Any]:
        """Run vision only on a baseline and already-selected transitions."""
        timeline = Timeline(task=task, recording=recording, started=baseline.get("ts"))
        baseline_result = self._call_vision(
            decode_frame(baseline),
            "Describe the visible application and state concisely in one sentence. Mention any dialog or error.",
        )
        baseline_description = baseline_result.get("description") or "Recording started"
        timeline.add(
            baseline.get("offset", 0.0),
            "initial_state",
            baseline_description,
            baseline_result.get("confidence", 0.0),
            baseline.get("ts"),
            self._evidence(baseline, recording),
        )

        semantic_failures: list[str] = []
        vision_unavailable = False
        if baseline_result.get("error"):
            semantic_failures.append(str(baseline_result["error"]))
            # Connection/model errors generally apply to the whole recording;
            # avoid repeating a slow failed request for every selected frame.
            vision_unavailable = True
        for event in detected:
            composite = self._composite(event["before_frame"], event["after_frame"])
            prompt = (
                "The left half is BEFORE and the right half is AFTER. Describe only the meaningful UI transition "
                "in one concise sentence: application/dialog opened or closed, command/result, error, progress, "
                "or button/text state change. Do not merely say that pixels changed."
            )
            result = ({"success": False, "error": semantic_failures[0]}
                      if vision_unavailable else self._call_vision(composite, prompt))
            description = result.get("description")
            if not description:
                description = f"Screen changed (score {event.get('change_score', 0.0):.3f})"
                if result.get("error"):
                    semantic_failures.append(str(result["error"]))
            timeline.add(
                event.get("offset") or 0.0,
                "visual_event",
                description,
                result.get("confidence", 0.0),
                event.get("ts"),
                self._evidence(event, recording),
            )

        json_events = [self.event_detector.json_event(event) for event in detected]
        return {
            "success": True,
            "semantic": bool(self.vision_model) and not semantic_failures,
            "semantic_errors": list(dict.fromkeys(semantic_failures)),
            "timeline": timeline.to_dict(),
            "timeline_text": timeline.render_text(),
            "events": json_events,
            "frames_analyzed": 1 + len(detected),
            "frames_total": frames_total,
        }

    def analyze(
        self,
        frames: list[dict[str, Any]],
        task: str = "",
        max_events: int = 12,
        recording: Optional[str] = None,
    ) -> dict[str, Any]:
        if not frames:
            timeline = Timeline(task=task, recording=recording)
            return {
                "success": False,
                "error": "no frames to analyze",
                "timeline": timeline.to_dict(),
                "events": [],
            }
        detected = self.event_detector.detect(frames, max_events=max_events)
        return self._analyze_detected(
            frames[0], detected, task, recording, frames_total=len(frames)
        )

    @staticmethod
    def _match_result(response: dict[str, Any]) -> dict[str, Any]:
        if not response.get("success"):
            return {"matched": False, "confidence": 0.0, "detail": response.get("error", "vision failed"), "error": response.get("error")}
        text = response.get("description", "")
        explicit = re.search(r"MATCH\s*:\s*(YES|NO)", text, flags=re.IGNORECASE)
        if explicit:
            matched = explicit.group(1).upper() == "YES"
        elif "matched" in response:
            matched = bool(response.get("matched"))
        else:
            matched = False
        return {
            "matched": matched,
            "confidence": float(response.get("confidence", 0.65)),
            "detail": text,
        }

    def observe_world(self, frame: Any) -> dict[str, Any]:
        """Produce one structured observation for the shared WorldState."""
        response = self._call_vision(
            decode_frame(frame),
            "Inspect the current desktop. Return ONLY JSON with keys: "
            "active_application (string or null), active_window (string or null), "
            "visible_targets (short list of visible actionable controls), dialogs "
            "(list of visible popups/dialogs), task_state (short state label), "
            "confidence (0 to 1), detail (one evidence sentence).",
        )
        if not response.get("success"):
            return {"confidence": 0.0, "detail": response.get("error", "vision failed"),
                    "source": "vision_error"}
        text = str(response.get("description") or "").strip()
        parsed = None
        decoder = __import__("json").JSONDecoder()
        start = text.find("{")
        if start >= 0:
            try:
                parsed, _ = decoder.raw_decode(text[start:])
            except Exception:
                parsed = None
        if isinstance(parsed, dict):
            parsed.setdefault("detail", text)
            parsed.setdefault("confidence", response.get("confidence", 0.65))
            parsed["source"] = "vision"
            return parsed
        return {
            "detail": text,
            "confidence": response.get("confidence", 0.65),
            "source": "vision_prose",
        }

    def evaluate_condition(self, frame: Any, condition: str) -> dict[str, Any]:
        """Ask the vision model whether a visual condition is currently true."""
        response = self._call_vision(
            decode_frame(frame),
            f"Condition: {condition}\nAnswer first with MATCH: YES or MATCH: NO, then one short evidence sentence.",
        )
        return self._match_result(response)

    def evaluate_transition(self, before: Any, after: Any, expected: str) -> dict[str, Any]:
        """Semantically verify an expected BEFORE → AFTER UI transition."""
        response = self._call_vision(
            self._composite(before, after),
            f"The left side is BEFORE and right side is AFTER. Expected transition: {expected}\n"
            "Answer first with MATCH: YES or MATCH: NO, then one short evidence sentence describing what changed.",
        )
        return self._match_result(response)

    def _select_video_events(
        self,
        video_path: str,
        sample_fps: float,
        max_seconds: float,
        max_events: int,
    ) -> dict[str, Any]:
        """Stream JPEGs from FFmpeg and retain only baseline + top events."""
        source = Path(video_path).expanduser().resolve()
        if not source.exists():
            return {"success": False, "error": f"video not found: {source}"}
        ffmpeg = VideoWriter.find_ffmpeg()
        if not ffmpeg:
            return {"success": False, "error": "FFmpeg unavailable; cannot read video"}
        fps = max(0.1, min(float(sample_fps), 10.0))
        max_events = max(0, int(max_events))
        max_frames = max(1, int(float(max_seconds) * fps))
        command = [
            ffmpeg,
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            f"fps={fps}",
            "-frames:v",
            str(max_frames),
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        error_file = tempfile.TemporaryFile()
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error_file)
        except Exception as exc:
            error_file.close()
            return {"success": False, "error": f"could not start FFmpeg: {exc}"}

        threshold = self.event_detector.sampler.threshold
        debounce = self.event_detector.debounce_seconds
        baseline: Optional[dict[str, Any]] = None
        previous: Optional[dict[str, Any]] = None
        pending: Optional[dict[str, Any]] = None
        selected: list[Any] = []  # min-heap: (score, order, event)
        order = 0
        count = 0

        def finalize(event: Optional[dict[str, Any]]) -> None:
            nonlocal order
            if event is None or max_events <= 0:
                return
            event.pop("_last_change_offset", None)
            order += 1
            item = (float(event.get("change_score", 0.0)), order, event)
            if len(selected) < max_events:
                heapq.heappush(selected, item)
            elif item[0] > selected[0][0]:
                heapq.heapreplace(selected, item)

        def consume(data: bytes) -> None:
            nonlocal baseline, previous, pending, count
            count += 1
            offset = (count - 1) / fps
            frame = {
                "ts": None,
                "captured_at": offset,
                "offset": round(offset, 4),
                "sequence": count,
                "encoding": "jpeg",
                "bytes": len(data),
                "data": data,
            }
            if baseline is None:
                baseline = frame
                previous = frame
                return
            score = _image_diff(previous, frame)
            before = previous
            previous = frame
            if score < threshold:
                return
            event = {
                "type": "screen_change",
                "ts": None,
                "offset": frame["offset"],
                "sequence": count,
                "change_score": round(score, 4),
                "frame_index": count - 1,
                "before_index": count - 2,
                "after_index": count - 1,
                "burst_frames": 1,
                "before_frame": before,
                "frame": frame,
                "after_frame": frame,
                "_last_change_offset": frame["offset"],
            }
            if pending is not None and frame["offset"] - pending["_last_change_offset"] <= debounce:
                last_offset = frame["offset"]
                if event["change_score"] >= pending["change_score"]:
                    event["burst_frames"] = pending.get("burst_frames", 1) + 1
                    event["_last_change_offset"] = last_offset
                    pending = event
                else:
                    pending["burst_frames"] = pending.get("burst_frames", 1) + 1
                    pending["after_frame"] = frame
                    pending["after_index"] = count - 1
                    pending["_last_change_offset"] = last_offset
            else:
                finalize(pending)
                pending = event

        buffer = bytearray()
        try:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start < 0:
                        if len(buffer) > 1:
                            del buffer[:-1]
                        break
                    end = buffer.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        if start:
                            del buffer[:start]
                        break
                    consume(bytes(buffer[start:end + 2]))
                    del buffer[:end + 2]
            return_code = process.wait(timeout=max(30.0, float(max_seconds) * 2))
            error_file.seek(0)
            stderr = error_file.read()
            error_file.close()
        except Exception as exc:
            process.kill()
            process.wait(timeout=5)
            error_file.close()
            return {"success": False, "error": f"video stream analysis failed: {exc}"}
        finalize(pending)
        if return_code != 0:
            error = stderr.decode("utf-8", errors="replace")[-1000:]
            return {"success": False, "error": error or f"FFmpeg exited {return_code}"}
        if baseline is None:
            return {"success": False, "error": "video contained no readable frames"}
        events = [item[2] for item in selected]
        events.sort(key=lambda event: event.get("frame_index", 0))
        return {
            "success": True,
            "baseline": baseline,
            "events": events,
            "frames_total": count,
            "sample_fps": fps,
            "video": str(source),
        }

    @staticmethod
    def frames_from_video(video_path: str, sample_fps: float = 2.0, max_seconds: float = 300.0) -> dict[str, Any]:
        """Extract a low-FPS analysis stream from an MP4/WebM using FFmpeg."""
        source = Path(video_path).expanduser().resolve()
        if not source.exists():
            return {"success": False, "error": f"video not found: {source}", "frames": []}
        ffmpeg = VideoWriter.find_ffmpeg()
        if not ffmpeg:
            return {"success": False, "error": "FFmpeg unavailable; cannot read video", "frames": []}
        fps = max(0.1, min(float(sample_fps), 10.0))
        max_frames = max(1, int(float(max_seconds) * fps))
        with tempfile.TemporaryDirectory(prefix="hermus-video-") as directory:
            pattern = str(Path(directory) / "frame-%06d.jpg")
            command = [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                f"fps={fps}",
                "-frames:v",
                str(max_frames),
                "-q:v",
                "4",
                pattern,
            ]
            completed = subprocess.run(command, capture_output=True, timeout=max(30.0, float(max_seconds) * 2))
            if completed.returncode != 0:
                error = completed.stderr.decode("utf-8", errors="replace")[-1000:]
                return {"success": False, "error": error or "FFmpeg frame extraction failed", "frames": []}
            frames: list[dict[str, Any]] = []
            for index, path in enumerate(sorted(Path(directory).glob("frame-*.jpg"))):
                data = path.read_bytes()
                offset = index / fps
                frames.append({
                    "ts": None,
                    "captured_at": offset,
                    "offset": round(offset, 4),
                    "sequence": index + 1,
                    "encoding": "jpeg",
                    "bytes": len(data),
                    "data": data,
                })
        return {"success": True, "frames": frames, "sample_fps": fps, "video": str(source)}

    def analyze_video(
        self,
        video_path: str,
        task: str = "",
        sample_fps: float = 2.0,
        max_seconds: float = 3600.0,
        max_events: int = 12,
    ) -> dict[str, Any]:
        selected = self._select_video_events(
            video_path,
            sample_fps=sample_fps,
            max_seconds=max_seconds,
            max_events=max_events,
        )
        if not selected.get("success"):
            return selected
        return self._analyze_detected(
            selected["baseline"],
            selected["events"],
            task,
            selected["video"],
            frames_total=selected["frames_total"],
        )
