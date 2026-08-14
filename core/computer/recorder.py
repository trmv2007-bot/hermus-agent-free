"""Rolling screen recorder — keeps the last N seconds of frames in memory.

Rather than streaming every frame to a vision model (expensive), the recorder
maintains a bounded deque of timestamped frames. The frame sampler (see
``frame_sampler``) then pulls only the *important* frames out for analysis.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


class ScreenSource:
    """Abstract screen capture source. ``capture()`` → PIL Image or None."""

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


class ScreenRecorder:
    def __init__(self, source: Optional[ScreenSource] = None, max_seconds: float = 30.0,
                 fps: float = 10.0):
        self.source = source or ImageGrabSource()
        self.max_seconds = max_seconds
        self.fps = fps
        self.interval = 1.0 / max(1.0, fps)
        self._frames: deque = deque()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._started: Optional[float] = None

    @property
    def running(self) -> bool:
        return self._running

    def _loop(self) -> None:
        while self._running:
            frame = self.source.capture()
            with self._lock:
                self._frames.append({"ts": datetime.now().isoformat(), "image": frame})
                self._prune()
            time.sleep(self.interval)

    def _prune(self) -> None:
        max_frames = int(self.max_seconds * self.fps)
        while len(self._frames) > max_frames:
            self._frames.popleft()

    def start(self) -> Dict[str, Any]:
        if self._running:
            return {"success": False, "error": "recorder already running"}
        self._running = True
        self._started = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return {"success": True, "status": "recording", "max_seconds": self.max_seconds, "fps": self.fps}

    def stop(self) -> Dict[str, Any]:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            n = len(self._frames)
        return {"success": True, "status": "stopped", "frames_buffered": n}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            n = len(self._frames)
        return {"running": self._running, "frames_buffered": n, "max_seconds": self.max_seconds,
                "fps": self.fps, "elapsed": round(time.time() - self._started, 2) if self._started else 0.0}

    def recent(self, seconds: float = 10.0) -> List[Dict[str, Any]]:
        """Return frames from the last ``seconds`` seconds (newest last)."""
        cutoff = time.time() - seconds
        out = []
        with self._lock:
            for f in self._frames:
                try:
                    dt = datetime.fromisoformat(f["ts"])
                    if dt.timestamp() >= cutoff:
                        out.append(f)
                except (ValueError, TypeError):
                    out.append(f)
        return out

    def all_frames(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._frames)

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()
