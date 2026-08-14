"""FFmpeg-backed MP4/WebM writer for compressed recorder frames.

FFmpeg is resolved from ``HERMUS_FFMPEG``, the system PATH, or the free
``imageio-ffmpeg`` package.  The recorder feeds an MJPEG image pipe, avoiding
raw full-screen frame copies and temporary per-frame files.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .recorder import encode_image


class VideoWriter:
    SUPPORTED = {".mp4", ".webm"}

    def __init__(
        self,
        output_path: str,
        fps: float = 10.0,
        ffmpeg_binary: Optional[str] = None,
        queue_size: int = 30,
    ):
        self.output_path = Path(output_path).expanduser().resolve()
        self.fps = max(0.1, min(float(fps), 60.0))
        self.ffmpeg_binary = ffmpeg_binary or self.find_ffmpeg()
        self.queue_size = max(1, int(queue_size))
        self._queue: queue.Queue = queue.Queue(maxsize=self.queue_size)
        self._process: Optional[subprocess.Popen] = None
        self._stderr_file: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._error: Optional[str] = None
        self.frames_written = 0
        self.frames_dropped = 0

    @staticmethod
    def find_ffmpeg() -> Optional[str]:
        configured = os.environ.get("HERMUS_FFMPEG")
        if configured and Path(configured).expanduser().exists():
            return str(Path(configured).expanduser())
        binary = shutil.which("ffmpeg")
        if binary:
            return binary
        try:
            import imageio_ffmpeg

            candidate = imageio_ffmpeg.get_ffmpeg_exe()
            if candidate and Path(candidate).exists():
                return candidate
        except Exception:
            pass
        return None

    @classmethod
    def available(cls) -> Dict[str, Any]:
        binary = cls.find_ffmpeg()
        return {
            "available": bool(binary),
            "ffmpeg": binary,
            "install": None if binary else "pip install imageio-ffmpeg (or install ffmpeg)",
        }

    def _command(self) -> list:
        extension = self.output_path.suffix.lower()
        if extension == ".mp4":
            codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        else:
            codec = ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-pix_fmt", "yuv420p"]
        return [
            str(self.ffmpeg_binary),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-framerate",
            str(self.fps),
            "-i",
            "pipe:0",
            "-an",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            *codec,
            str(self.output_path),
        ]

    def start(self) -> Dict[str, Any]:
        if self._running:
            return {"success": False, "error": "video writer already running"}
        if self.output_path.suffix.lower() not in self.SUPPORTED:
            return {"success": False, "error": "video output must end in .mp4 or .webm"}
        if not self.ffmpeg_binary:
            return {
                "success": False,
                "error": "FFmpeg unavailable; install imageio-ffmpeg or ffmpeg",
            }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        # Create with private permissions before FFmpeg truncates it; opening an
        # existing file preserves this mode instead of exposing screen content
        # under the process umask while recording is active.
        self.output_path.touch(mode=0o600, exist_ok=True)
        try:
            self.output_path.chmod(0o600)
        except OSError:
            pass
        try:
            # A file avoids the classic stderr-pipe deadlock when FFmpeg emits
            # many decoder/encoder errors during a long session.
            self._stderr_file = tempfile.TemporaryFile()
            self._process = subprocess.Popen(
                self._command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
            )
        except Exception as exc:
            self._process = None
            if self._stderr_file:
                self._stderr_file.close()
                self._stderr_file = None
            return {"success": False, "error": f"could not start FFmpeg: {exc}"}
        self._running = True
        self._thread = threading.Thread(target=self._drain, daemon=True, name="hermus-video-writer")
        self._thread.start()
        return {"success": True, "status": "writing", "path": str(self.output_path), "fps": self.fps}

    @staticmethod
    def _frame_bytes(frame: Any) -> Optional[bytes]:
        if isinstance(frame, dict):
            data = frame.get("data")
            if data is not None:
                return bytes(data)
            frame = frame.get("image")
        return encode_image(frame)

    def write(self, frame: Any) -> bool:
        """Queue a frame without blocking capture; return False if it was dropped."""
        if not self._running:
            return False
        data = self._frame_bytes(frame)
        if not data:
            self.frames_dropped += 1
            return False
        try:
            self._queue.put_nowait(data)
            return True
        except queue.Full:
            self.frames_dropped += 1
            return False

    def _drain(self) -> None:
        while self._running or not self._queue.empty():
            try:
                data = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if self._process is None or self._process.stdin is None:
                    raise BrokenPipeError("FFmpeg stdin unavailable")
                self._process.stdin.write(data)
                self.frames_written += 1
            except Exception as exc:
                self._error = f"FFmpeg write failed: {exc}"
                self._running = False
            finally:
                self._queue.task_done()

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "path": str(self.output_path),
            "frames_written": self.frames_written,
            "frames_dropped": self.frames_dropped,
            "queue_depth": self._queue.qsize(),
            "error": self._error,
        }

    def close(self, timeout: float = 30.0) -> Dict[str, Any]:
        self._running = False
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        process = self._process
        self._process = None
        stderr = b""
        return_code = None
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait(timeout=5)
                self._error = "FFmpeg did not finalize before timeout"
            except Exception as exc:
                self._error = f"FFmpeg finalize failed: {exc}"
        if self._stderr_file:
            try:
                self._stderr_file.seek(0)
                stderr = self._stderr_file.read()
            finally:
                self._stderr_file.close()
                self._stderr_file = None
        if return_code not in (None, 0) and not self._error:
            self._error = (stderr.decode("utf-8", errors="replace") or f"FFmpeg exited {return_code}")[-1000:]
        exists = self.output_path.exists() and self.output_path.stat().st_size > 0
        success = not self._error and return_code == 0 and exists
        return {
            "success": success,
            "path": str(self.output_path),
            "frames_written": self.frames_written,
            "frames_dropped": self.frames_dropped,
            "bytes": self.output_path.stat().st_size if exists else 0,
            "error": self._error if not success else None,
        }

    @classmethod
    def write_frames(
        cls,
        output_path: str,
        frames: Iterable[Any],
        fps: float = 10.0,
        ffmpeg_binary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Encode a finite iterable, blocking rather than dropping frames."""
        writer = cls(output_path, fps=fps, ffmpeg_binary=ffmpeg_binary, queue_size=1)
        opened = writer.start()
        if not opened.get("success"):
            return opened
        # Bypass the live queue so a finite save never silently loses evidence.
        count = 0
        try:
            for frame in frames:
                data = writer._frame_bytes(frame)
                if not data:
                    continue
                if writer._process is None or writer._process.stdin is None:
                    raise BrokenPipeError("FFmpeg stdin unavailable")
                writer._process.stdin.write(data)
                writer.frames_written += 1
                count += 1
        except Exception as exc:
            writer._error = f"FFmpeg write failed: {exc}"
        # No queued work exists; stop the worker before finalization.
        result = writer.close()
        result["input_frames"] = count
        return result
