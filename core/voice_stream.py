"""Low-latency voice turn state and authoritative interruption primitives."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VoiceStream:
    id: str
    session_id: str
    state: str = "listening"
    started_at: float = field(default_factory=time.time)
    chunks: int = 0
    output_generation: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class VoiceStreamManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._streams = {}

    def start(self, session_id: str, **metadata: Any) -> dict[str, Any]:
        with self._lock:
            s = VoiceStream(f"vs_{uuid.uuid4().hex[:12]}", session_id, metadata=dict(metadata))
            self._streams[s.id] = s
            return self.snapshot(s.id)

    def push(self, stream_id: str, chunk_bytes: int = 0) -> dict[str, Any]:
        with self._lock:
            s = self._streams[str(stream_id)]
            if s.state != "listening":
                raise ValueError("stream is not listening")
            s.chunks += 1
            return self.snapshot(stream_id)

    def begin_speaking(self, stream_id: str) -> dict[str, Any]:
        with self._lock:
            self._streams[str(stream_id)].state = "speaking"
            return self.snapshot(stream_id)

    def interrupt(self, stream_id: str) -> dict[str, Any]:
        with self._lock:
            s = self._streams[str(stream_id)]
            s.state = "interrupted"
            s.output_generation += 1
            return self.snapshot(stream_id)

    def close(self, stream_id: str) -> dict[str, Any]:
        with self._lock:
            self._streams[str(stream_id)].state = "closed"
            return self.snapshot(stream_id)

    def snapshot(self, stream_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            rows = [self._streams[stream_id]] if stream_id and stream_id in self._streams else list(self._streams.values())
            return {
                "streams": [
                    {
                        "id": s.id,
                        "session_id": s.session_id,
                        "state": s.state,
                        "started_at": s.started_at,
                        "chunks": s.chunks,
                        "output_generation": s.output_generation,
                        "metadata": s.metadata,
                    }
                    for s in rows
                ]
            }


voice_streams = VoiceStreamManager()
