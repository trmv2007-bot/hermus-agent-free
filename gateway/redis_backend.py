"""Optional Redis Streams transport for the gateway job queue.

Enabled with ``HERMUS_QUEUE_BACKEND=redis`` + ``REDIS_URL`` and the ``redis``
package installed. Purpose: hand jobs between processes (gateway + N detached
workers) with at-least-once delivery via consumer groups, and let a worker
crash be visible (pending list) instead of silent.

When anything is missing — no redis package, no server, bad URL — ``start()``
raises and :class:`gateway.queue.JobQueue` prints a notice and keeps using its
in-process lanes. The agent must stay runnable on a laptop with zero services,
so this is an accelerator, never a requirement.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from core.config import config

STREAM = "hermus:jobs"
GROUP = "hermus-workers"


def redis_available(url: str = "") -> tuple:
    """``(ok, detail)`` — is the optional Redis transport usable right now?

    Checked before the consumer is built so the gateway prints one clear reason
    ("redis package not installed" / "connection refused") instead of a stack trace.
    """
    target = url or getattr(config, "redis_url", None) or "redis://localhost:6379/0"
    try:
        import redis  # noqa: F401  (sync client used only for the probe)
    except Exception:
        return False, "redis package not installed — pip install redis (optional)"
    try:
        client = redis.from_url(target, socket_connect_timeout=1.5, decode_responses=True)
        pong = bool(client.ping())
        try:
            client.close()
        except Exception:
            pass
        if pong:
            return True, f"ping ok ({_redact(target)})"
        return False, f"no PING reply ({_redact(target)})"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]} ({_redact(target)})"


class RedisStreamsConsumer:
    """Publish/consume job records over Redis Streams (consumer group)."""

    def __init__(self, queue, *, stream: str = STREAM, group: str = GROUP):
        self.queue = queue
        self.stream = stream
        self.group = group
        self._redis = None
        self._task: Optional[asyncio.Task] = None
        self._consumer = f"gw-{id(self):x}"

    async def start(self) -> Dict[str, Any]:
        import redis.asyncio as aioredis  # raises ImportError → caller falls back

        url = getattr(config, "redis_url", None) or "redis://localhost:6379/0"
        self._redis = aioredis.from_url(url, decode_responses=True)
        try:
            await self._redis.xinfo_groups(self.stream)
        except Exception:
            try:
                await self._redis.xgroup_create(self.stream, self.group, id="$", mkstream=True)
            except Exception:
                pass  # group exists (race) — fine
        self._task = asyncio.create_task(self._loop())
        return {"backend": "redis", "stream": self.stream, "group": self.group, "url": _redact(url)}

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

    async def publish(self, job_id: str, kind: str, payload: Dict[str, Any],
                      session_key: str = "") -> Optional[str]:
        if self._redis is None:
            return None
        body = {"job_id": job_id, "kind": kind, "session_key": session_key,
                "payload": json.dumps(payload or {}, default=str)}
        return await self._redis.xadd(self.stream, body, maxlen=10_000, approximate=True)

    async def _loop(self) -> None:
        assert self._redis is not None
        while True:
            try:
                resp = await self._redis.xreadgroup(
                    self.group, self._consumer, {self.stream: ">"}, count=4, block=2000
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await asyncio.sleep(1.0)
                continue
            for _stream, entries in resp or []:
                for entry_id, fields in entries:
                    await self._handle(entry_id, fields)
            await asyncio.sleep(0)

    async def _handle(self, entry_id: str, fields: Dict[str, Any]) -> None:
        job_id = str(fields.get("job_id") or "")
        try:
            payload = json.loads(fields.get("payload") or "{}")
        except Exception:
            payload = {}
        try:
            # Local lane executes it (keeps one execution path for both transports).
            self.queue.submit(str(fields.get("kind") or "agent.chat"), payload,
                              session_key=str(fields.get("session_key") or "default"),
                              job_id=job_id or None)
        except Exception as e:
            self.queue._record({"job_id": job_id, "event": "redis_dispatch_failed", "error": str(e)[:300]})
        finally:
            try:
                await self._redis.xack(self.stream, self.group, entry_id)
            except Exception:
                pass


def _redact(url: str) -> str:
    if "@" in url:
        head, _, tail = url.partition("@")
        scheme = head.split("://")[0]
        return f"{scheme}://***@{tail}"
    return url
