"""Memory-efficient rolling screen recorder.

The recorder owns capture only.  It keeps a short, JPEG-compressed rolling
buffer in RAM and can fan the same compressed frames out to ``VideoWriter`` for
an optional full MP4/WebM recording.  Vision code decodes only the frames it
selects, rather than retaining hundreds of full-size PIL images.
"""
from __future__ import annotations

import io
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional


class ScreenSource:
    """Abstract screen capture source. ``capture()`` returns a PIL image/bytes."""

    def capture(self) -> Any:
        raise NotImplementedError


class ImageGrabSource(ScreenSource):
    """Real screen capture via PIL.ImageGrab (best-effort, headless-safe)."""

    def capture(self) -> Any:
        try:
            from PIL import ImageGrab

            return ImageGrab.grab()
        except Exception:
            return None


class NullSource(ScreenSource):
    """No-op source (headless / tests / explicit opt-out)."""

    def capture(self) -> Any:
        return None


class CallableSource(ScreenSource):
    """Wrap any callable returning a frame (tests, injection)."""

    def __init__(self, fn: Callable[[], Any]):
        self.fn = fn

    def capture(self) -> Any:
        return self.fn()


def encode_image(image: Any, quality: int = 70, max_width: Optional[int] = None) -> Optional[bytes]:
    """Encode a PIL-compatible image as JPEG bytes.

    ``bytes`` are accepted too, which makes synthetic/test sources cheap.  A
    copy is resized so capture sources never have their image mutated.
    """
    if image is None:
        return None
    if isinstance(image, (bytes, bytearray, memoryview)):
        raw = bytes(image)
        if raw.startswith(b"\xff\xd8"):
            return raw
        try:
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as opened:
                image = opened.convert("RGB").copy()
        except Exception:
            return None
    try:
        img = image.convert("RGB")
        if max_width and img.width > max_width:
            ratio = max_width / float(img.width)
            size = (max_width, max(1, round(img.height * ratio)))
            try:
                from PIL import Image

                img = img.resize(size, Image.Resampling.LANCZOS)
            except (ImportError, AttributeError):
                img = img.resize(size)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=max(20, min(int(quality), 95)), optimize=False)
        return out.getvalue()
    except Exception:
        return None


def decode_frame(frame: Any) -> Any:
    """Return a detached PIL image from a frame record, bytes, or PIL image."""
    value = frame
    if isinstance(frame, dict):
        if frame.get("image") is not None:  # backward-compatible raw frame
            value = frame["image"]
        else:
            value = frame.get("data")
    if value is None:
        return None
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return value
    try:
        from PIL import Image

        with Image.open(io.BytesIO(bytes(value))) as img:
            return img.convert("RGB").copy()
    except Exception:
        return None


class ScreenRecorder:
    """Threaded capture with a bounded compressed-frame rolling buffer.

    Parameters are intentionally conservative.  ``max_buffer_mb`` is a hard
    memory guard in addition to the FPS/duration bound.  When ``output_path``
    is passed to :meth:`start`, all captured frames are also streamed to an
    FFmpeg-backed video writer while only the recent window remains in RAM.
    """

    def __init__(
        self,
        source: Optional[ScreenSource] = None,
        max_seconds: float = 30.0,
        fps: float = 10.0,
        jpeg_quality: int = 70,
        max_width: Optional[int] = None,
        max_buffer_mb: float = 128.0,
        writer_factory: Optional[Callable[..., Any]] = None,
    ):
        self.source = source or ImageGrabSource()
        self.max_seconds = float(max_seconds)
        self.fps = float(fps)
        self.jpeg_quality = int(jpeg_quality)
        self.max_width = max_width
        self.max_buffer_bytes = max(1, int(float(max_buffer_mb) * 1024 * 1024))
        self._writer_factory = writer_factory

        self._frames: Deque[Dict[str, Any]] = deque()
        self._buffer_bytes = 0
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._started: Optional[float] = None
        self._started_monotonic: Optional[float] = None
        self._sequence = 0
        self._captured = 0
        self._dropped = 0
        self._capture_errors = 0
        self._writer: Optional[Any] = None
        self._last_video: Optional[Dict[str, Any]] = None
        self._markers: List[Dict[str, Any]] = []

    @property
    def running(self) -> bool:
        return self._running

    @property
    def interval(self) -> float:
        return 1.0 / max(0.1, self.fps)

    def _make_record(self, image: Any, now_wall: float, now_mono: float) -> Optional[Dict[str, Any]]:
        data = encode_image(image, quality=self.jpeg_quality, max_width=self.max_width)
        if not data:
            return None
        self._sequence += 1
        size = None
        try:
            size = [int(image.width), int(image.height)]
            if self.max_width and size[0] > self.max_width:
                ratio = self.max_width / float(size[0])
                size = [self.max_width, max(1, round(size[1] * ratio))]
        except Exception:
            pass
        return {
            "ts": datetime.fromtimestamp(now_wall).astimezone().isoformat(),
            "captured_at": now_wall,
            "offset": round(now_mono - (self._started_monotonic or now_mono), 4),
            "sequence": self._sequence,
            "encoding": "jpeg",
            "size": size,
            "bytes": len(data),
            "data": data,
        }

    def _capture(self) -> Optional[Dict[str, Any]]:
        try:
            image = self.source.capture()
        except Exception:
            self._capture_errors += 1
            return None
        if image is None:
            self._dropped += 1
            return None
        now_wall, now_mono = time.time(), time.monotonic()
        record = self._make_record(image, now_wall, now_mono)
        if record is None:
            self._capture_errors += 1
        return record

    def _append(self, record: Dict[str, Any]) -> None:
        with self._lock:
            self._frames.append(record)
            self._buffer_bytes += int(record.get("bytes") or 0)
            self._captured += 1
            self._prune(record.get("captured_at", time.time()))

    def _loop(self) -> None:
        next_capture = time.monotonic()
        while self._running:
            record = self._capture()
            if record is not None:
                self._append(record)
                writer = self._writer
                if writer is not None and not writer.write(record):
                    self._dropped += 1
            next_capture += self.interval
            delay = next_capture - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                # If capture/encoding fell behind, resume from now rather than
                # creating a CPU-heavy catch-up burst.
                next_capture = time.monotonic()

    def _prune(self, newest_at: Optional[float] = None) -> None:
        newest_at = newest_at or time.time()
        cutoff = newest_at - max(0.1, self.max_seconds)
        max_frames = max(1, int(self.max_seconds * max(0.1, self.fps)) + 1)
        while self._frames and (
            len(self._frames) > max_frames
            or float(self._frames[0].get("captured_at", newest_at)) < cutoff
            or (self._buffer_bytes > self.max_buffer_bytes and len(self._frames) > 1)
        ):
            old = self._frames.popleft()
            self._buffer_bytes = max(0, self._buffer_bytes - int(old.get("bytes") or 0))

    def start(
        self,
        max_seconds: Optional[float] = None,
        fps: Optional[float] = None,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start capture, optionally streaming the full session to MP4/WebM."""
        with self._lock:
            if self._running:
                return {"success": False, "error": "recorder already running"}
            if max_seconds is not None:
                self.max_seconds = max(1.0, float(max_seconds))
            if fps is not None:
                self.fps = max(0.1, min(float(fps), 60.0))

            if output_path:
                if self._writer_factory:
                    writer = self._writer_factory(output_path=output_path, fps=self.fps)
                else:
                    from .video_writer import VideoWriter

                    writer = VideoWriter(output_path=output_path, fps=self.fps)
                opened = writer.start()
                if not opened.get("success"):
                    return opened
                self._writer = writer

            # A start defines a new recording session; never mix evidence or
            # duplicate sequence numbers from a prior stopped task.
            self._frames.clear()
            self._buffer_bytes = 0
            self._running = True
            self._started = time.time()
            self._started_monotonic = time.monotonic()
            self._sequence = 0
            self._captured = 0
            self._dropped = 0
            self._capture_errors = 0
            self._last_video = None
            self._markers = []
            self._thread = threading.Thread(target=self._loop, daemon=True, name="hermus-screen-recorder")
            self._thread.start()
        return {
            "success": True,
            "status": "recording",
            "max_seconds": self.max_seconds,
            "fps": self.fps,
            "compression": f"jpeg:{self.jpeg_quality}",
            "output_path": output_path,
        }

    def stop(self) -> Dict[str, Any]:
        self._running = False
        if self._thread:
            self._thread.join(timeout=max(2.0, self.interval * 2))
            self._thread = None
        video = None
        writer = self._writer
        self._writer = None
        if writer is not None:
            video = writer.close()
            self._last_video = video
        with self._lock:
            n = len(self._frames)
            buffered = self._buffer_bytes
        result = {
            "success": bool(video is None or video.get("success")),
            "status": "stopped",
            "frames_buffered": n,
            "buffer_bytes": buffered,
            "frames_captured": self._captured,
            "frames_dropped": self._dropped,
        }
        if video is not None:
            result["video"] = video
            if not video.get("success"):
                result["error"] = video.get("error")
        return result

    def status(self) -> Dict[str, Any]:
        with self._lock:
            n, buffered = len(self._frames), self._buffer_bytes
        return {
            "running": self._running,
            "frames_buffered": n,
            "buffer_bytes": buffered,
            "buffer_mb": round(buffered / (1024 * 1024), 3),
            "max_buffer_mb": round(self.max_buffer_bytes / (1024 * 1024), 3),
            "max_seconds": self.max_seconds,
            "fps": self.fps,
            "compression": f"jpeg:{self.jpeg_quality}",
            "elapsed": round(time.time() - self._started, 2) if self._started else 0.0,
            "frames_captured": self._captured,
            "frames_dropped": self._dropped,
            "capture_errors": self._capture_errors,
            "video": self._writer.status() if self._writer is not None else self._last_video,
        }

    def recent(self, seconds: float = 10.0) -> List[Dict[str, Any]]:
        """Return compressed records from the last ``seconds`` (newest last)."""
        cutoff = time.time() - max(0.0, float(seconds))
        with self._lock:
            return [f for f in self._frames if float(f.get("captured_at", 0.0)) >= cutoff]

    def all_frames(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._frames)

    def latest(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._frames[-1] if self._frames else None

    def capture_now(self, store: bool = True) -> Optional[Dict[str, Any]]:
        """Capture an explicit action boundary frame (BEFORE or AFTER)."""
        record = self._capture()
        if record is not None and store:
            self._append(record)
            if self._writer is not None:
                self._writer.write(record)
        return record

    def mark(self, label: str, kind: str = "action", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Add a lightweight action marker tied to the current recording time."""
        marker = {
            "id": len(self._markers) + 1,
            "label": label,
            "type": kind,
            "ts": datetime.now().astimezone().isoformat(),
            "offset": round(time.monotonic() - (self._started_monotonic or time.monotonic()), 4),
            "sequence": self._sequence,
            "metadata": metadata or {},
        }
        with self._lock:
            self._markers.append(marker)
        return marker

    def markers(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._markers)

    def save(self, output_path: str, seconds: Optional[float] = None) -> Dict[str, Any]:
        """Encode the current rolling buffer as a conventional MP4/WebM file."""
        frames = self.recent(seconds) if seconds is not None else self.all_frames()
        if not frames:
            return {"success": False, "error": "no frames buffered"}
        from .video_writer import VideoWriter

        return VideoWriter.write_frames(output_path, frames, fps=self.fps)

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()
            self._buffer_bytes = 0
            self._markers = []
