"""Controlled session abstraction over Scrapling's persistent sessions (spec §12).

Rules enforced here (non-negotiable):

* cookies live in Scrapling session objects, in memory only — they are NEVER
  serialized, logged, or exposed to the LLM (describe() reports counts only);
* a session is pinned to an explicit set of allowed domains at creation time —
  a session cannot be pointed at a domain it was not created for, so cookies
  can never leak across domains through Hermus;
* sessions expire (TTL) and can be destroyed; cleanup drops the object so the
  cookie jar is garbage-collected;
* session names are agent-chosen labels, never raw cookie identifiers.

The scrapling session objects themselves are created by the backend module —
this manager owns their lifecycle and isolation policy.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from .errors import SecurityBlockedError
from .errors import WebAcquisitionError
from .security import WebSecurityPolicy, host_matches_pattern


class WebSessionError(WebAcquisitionError):
    def __init__(self, message: str):
        super().__init__(message, error_code="WEB_SESSION_ERROR", retryable=False)


@dataclass
class WebSession:
    """One named, domain-pinned acquisition session."""

    name: str
    allowed_domains: tuple[str, ...]
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)
    ttl_seconds: float = 1800.0
    sync_session: Optional[Any] = None      # live scrapling client (_SyncSessionLogic)
    _sync_factory: Optional[Any] = None     # FetcherSession context manager (owns the client)
    dynamic_session: Optional[Any] = None   # scrapling DynamicSession (lazy; optional)
    stealth_session: Optional[Any] = None   # scrapling StealthySession (lazy; optional)
    requests: int = 0

    def expired(self) -> bool:
        return (time.monotonic() - self.last_used_at) > self.ttl_seconds

    def domain_allowed(self, url: str) -> bool:
        host = (urlparse(url or "").hostname or "").lower().strip(".")
        if not host:
            return False
        return any(host_matches_pattern(host, p) for p in self.allowed_domains)

    def ensure_sync_session(self) -> Any:
        """Lazily open (once) and return the live Scrapling sync client.

        Scrapling's ``FetcherSession`` is a factory context manager: entering it
        yields the client that actually has ``.get()``. We keep that client for
        the session's lifetime so cookies persist across fetches, and close it
        in :meth:`close`.
        """
        if self.sync_session is not None:
            return self.sync_session
        from scrapling.fetchers import FetcherSession  # lazy: optional dependency

        factory = FetcherSession()
        client = factory.__enter__()  # noqa: PLC2801 - explicit lifecycle, closed in close()
        self._sync_factory = factory
        self.sync_session = client
        return client

    def close(self) -> None:
        factory, self._sync_factory = self._sync_factory, None
        self.sync_session = None
        self.dynamic_session = None
        self.stealth_session = None
        if factory is None:
            return
        try:
            factory.__exit__(None, None, None)  # noqa: PLC2801 - paired with __enter__ above
        except Exception:
            pass


class WebSessionManager:
    """Creates, isolates, expires and destroys acquisition sessions."""

    def __init__(self, policy: WebSecurityPolicy, *, max_sessions: int = 8,
                 ttl_seconds: float = 1800.0):
        self._policy = policy
        self._max_sessions = max(1, int(max_sessions))
        self._ttl = float(ttl_seconds)
        self._lock = threading.RLock()
        self._sessions: dict[str, WebSession] = {}

    # ------------------------------------------------------------------ CRUD
    def create(self, name: str, *, domains: list[str], strategy: str = "static") -> dict[str, Any]:
        """Create (or reuse) a named session pinned to ``domains``."""
        name = (name or "").strip()
        if not name or len(name) > 64:
            raise WebSessionError("session name must be 1-64 characters")
        clean_domains = []
        for domain in domains or []:
            domain = (domain or "").lower().strip(".")
            if not domain:
                continue
            if self._policy.blocked_domains and any(
                    host_matches_pattern(domain, p) for p in self._policy.blocked_domains):
                raise SecurityBlockedError(
                    f"session '{name}': domain '{domain}' is blocked by Hermus policy")
            clean_domains.append(domain)
        if not clean_domains:
            raise WebSessionError("sessions must pin at least one allowed domain")

        with self._lock:
            self._sweep()
            existing = self._sessions.get(name)
            if existing is not None:
                existing.last_used_at = time.monotonic()
                return self.describe(name)
            if len(self._sessions) >= self._max_sessions:
                oldest = min(self._sessions.values(), key=lambda s: s.last_used_at)
                self._destroy(oldest.name)
            session = WebSession(name=name, allowed_domains=tuple(clean_domains),
                                 ttl_seconds=self._ttl)
            if strategy in ("static", "any"):
                try:
                    session.ensure_sync_session()
                except ImportError as exc:
                    raise WebSessionError(
                        "cannot open scrapling session — install 'scrapling[fetchers]'"
                    ) from exc
                except Exception as exc:
                    raise WebSessionError(f"cannot open scrapling session: {exc}") from exc
            self._sessions[name] = session
            return self.describe(name)

    def get(self, name: str, *, url: str = "") -> WebSession:
        """Fetch a live session for use; enforces domain pinning + TTL."""
        with self._lock:
            session = self._sessions.get(name)
            if session is None:
                raise WebSessionError(f"session '{name}' does not exist")
            if session.expired():
                self._destroy(name)
                raise WebSessionError(f"session '{name}' expired (TTL)")
            if url and not session.domain_allowed(url):
                raise SecurityBlockedError(
                    f"session '{name}' is pinned to {list(session.allowed_domains)}; "
                    f"'{urlparse(url).hostname}' is outside it — refusing to avoid cross-domain cookie leaks"
                )
            session.last_used_at = time.monotonic()
            session.requests += 1
            return session

    def destroy(self, name: str) -> dict[str, Any]:
        with self._lock:
            if name not in self._sessions:
                return {"ok": False, "error": f"session '{name}' does not exist"}
            self._destroy(name)
            return {"ok": True, "destroyed": name}

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            self._sweep()
            return [self._describe(session) for session in self._sessions.values()]

    def describe(self, name: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(name)
            if session is None:
                return {"ok": False, "error": f"session '{name}' does not exist"}
            return self._describe(session)

    # -------------------------------------------------------------- internals
    def _destroy(self, name: str) -> None:
        session = self._sessions.pop(name, None)
        if session is not None:
            session.close()

    def _sweep(self) -> None:
        """Drop expired sessions (must be called under the lock)."""
        for name in [n for n, s in self._sessions.items() if s.expired()]:
            self._destroy(name)

    def _describe(self, session: WebSession) -> dict[str, Any]:
        # Cookie VALUES never leave here — only the shape (count/domain count).
        return {
            "ok": True,
            "name": session.name,
            "allowed_domains": list(session.allowed_domains),
            "age_seconds": int(time.monotonic() - session.created_at),
            "ttl_seconds": int(session.ttl_seconds),
            "requests": session.requests,
            "expired": session.expired(),
        }

    def close_all(self) -> None:
        with self._lock:
            for name in list(self._sessions):
                self._destroy(name)
