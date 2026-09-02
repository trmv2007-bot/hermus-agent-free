"""Normalized result + error models for the web acquisition subsystem.

These are the ONLY web objects the rest of Hermus is allowed to see. Raw
Scrapling responses (and their cookie jars / request headers) never leave
:mod:`core.web.scrapling_backend`; everything is flattened into a
:class:`WebResult` here so downstream consumers (agent, ModelGateway,
MemoryFacade) get a predictable, secrets-free, size-bounded shape.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FetchStrategy(str, Enum):
    """How a page was (or should be) acquired. Escalation order: cheapest first."""

    AUTO = "auto"          # let the router decide (never a final strategy)
    STATIC = "static"      # Scrapling Fetcher — fast HTTP with browser TLS fingerprint
    DYNAMIC = "dynamic"    # Scrapling DynamicFetcher — Playwright-driven Chromium
    STEALTH = "stealth"    # Scrapling StealthyFetcher — hardened anti-bot browser


class FailureClass(str, Enum):
    """Machine-readable classification of an acquisition failure (spec §6)."""

    NONE = "none"
    SECURITY_BLOCKED = "security_blocked"     # SSRF / policy / scheme / private IP
    DNS = "dns_failure"
    CONNECTION = "connection_error"
    TIMEOUT = "timeout"
    TLS = "tls_error"
    HTTP_STATUS = "http_status"               # server answered with an error status
    CHALLENGE = "bot_challenge"               # anti-bot interstitial detected
    EMPTY_CONTENT = "empty_content"           # page fetched but no meaningful text
    JS_REQUIRED = "js_required"               # content only rendered client-side
    DEPENDENCY_MISSING = "dependency_missing"  # scrapling / browser not installed
    CANCELLED = "cancelled"
    SIZE_LIMIT = "size_limit"
    UNKNOWN = "unknown"


class CrawlStatus(str, Enum):
    """Crawl job lifecycle (mirrors JobQueue vocabulary)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"        # wall-clock deadline reached before completion


@dataclass
class StrategyAttempt:
    """One acquisition attempt (strategy, outcome, timing) for observability."""

    strategy: str
    outcome: str                      # "success" | "insufficient" | "error" | "skipped"
    status_code: Optional[int] = None
    duration_ms: Optional[int] = None
    error_class: str = FailureClass.NONE.value
    error: str = ""
    reason: str = ""                  # why we escalated away from this strategy

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "outcome": self.outcome,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "error_class": self.error_class,
            "error": self.error[:300],
            "reason": self.reason[:200],
        }


@dataclass
class LinkInfo:
    """One extracted link (absolute URL + anchor text)."""

    url: str
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "text": self.text[:200]}


@dataclass
class ExtractionResult:
    """Outcome of one targeted extraction (spec §13) — value + how it was found."""

    ok: bool
    source_url: str = ""
    selector: str = ""
    method: str = ""                  # "css" | "xpath" | "text" | "markdown" | "links" | "metadata"
    values: list[str] = field(default_factory=list)
    adaptive: bool = False
    confidence: Optional[float] = None  # adaptive similarity confidence when known
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source_url": self.source_url,
            "selector": self.selector,
            "method": self.method,
            "values": [v[:2000] for v in self.values],
            "count": len(self.values),
            "adaptive": self.adaptive,
            "confidence": self.confidence,
            "error": self.error[:300],
            "warnings": list(self.warnings),
        }


@dataclass
class CrawlProgress:
    """Live progress snapshot for a crawl job (emitted onto the EventBus)."""

    pages_discovered: int = 0
    pages_processed: int = 0
    pages_failed: int = 0
    current_url: str = ""
    elapsed_ms: int = 0
    max_pages: int = 0
    status: str = CrawlStatus.QUEUED.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages_discovered": self.pages_discovered,
            "pages_processed": self.pages_processed,
            "pages_failed": self.pages_failed,
            "current_url": self.current_url,
            "elapsed_ms": self.elapsed_ms,
            "max_pages": self.max_pages,
            "estimated_progress": (
                round(min(1.0, self.pages_processed / max(1, self.max_pages)), 3)
                if self.max_pages else None
            ),
            "status": self.status,
        }


def content_hash(body: bytes) -> str:
    """Stable content hash for dedupe / cache validation."""
    return hashlib.sha256(body or b"").hexdigest()


@dataclass
class WebResult:
    """The one normalized acquisition result (spec §7).

    Secrets-free by construction: cookies, authorization headers, proxy
    credentials and raw response headers are deliberately NOT fields here.
    Raw HTML is optional and capped (``html``) to protect memory; text/markdown
    are capped at the configured LLM budget.
    """

    ok: bool
    url: str = ""
    final_url: str = ""
    title: str = ""
    status_code: Optional[int] = None
    content_type: str = ""
    strategy: str = FetchStrategy.AUTO.value
    fetched_at: str = ""
    duration_ms: Optional[int] = None
    text: str = ""
    markdown: str = ""
    html: Optional[str] = None
    links: list[LinkInfo] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    data: Optional[Any] = None                  # structured capture (e.g. captured XHR JSON)
    source: str = "scrapling"                   # which backend produced this
    attempts: list[StrategyAttempt] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    error_code: str = ""
    failure_class: str = FailureClass.NONE.value
    size_bytes: int = 0
    sha256: str = ""
    truncated: bool = False
    untrusted: bool = True                      # always: page content is untrusted data
    cached: bool = False
    session_name: str = ""

    # ---------------------------------------------------------------- output
    def to_dict(self, *, include_html: bool = False, include_markdown: bool = False,
                max_links: int = 50) -> dict[str, Any]:
        """Plain-dict view for tool output / model consumption (size-bounded)."""
        out: dict[str, Any] = {
            "ok": self.ok,
            "url": self.url,
            "final_url": self.final_url,
            "title": self.title[:300],
            "status_code": self.status_code,
            "content_type": self.content_type,
            "strategy": self.strategy,
            "fetched_at": self.fetched_at,
            "duration_ms": self.duration_ms,
            "text": self.text,
            "metadata": dict(self.metadata),
            "source": self.source,
            "attempts": [a.to_dict() for a in self.attempts],
            "warnings": list(self.warnings),
            "failure_class": self.failure_class,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "truncated": self.truncated,
            "untrusted": self.untrusted,
            "cached": self.cached,
            "links": [l.to_dict() for l in self.links[:max_links]],
            "links_total": len(self.links),
        }
        if self.error:
            out["error"] = self.error[:500]
        if self.error_code:
            out["error_code"] = self.error_code
        if include_markdown and self.markdown:
            out["markdown"] = self.markdown
        if include_html and self.html:
            out["html"] = self.html
        if self.data is not None:
            out["data"] = self.data
        return out

    def summary(self, max_chars: int = 1200) -> str:
        """One LLM-sized string summary (title + leading text)."""
        head = f"[untrusted web content — {self.final_url or self.url}] {self.title}".strip()
        body = " ".join((self.text or "").split())
        if len(body) > max_chars:
            body = body[:max_chars] + " …[truncated]"
        return f"{head}\n{body}".strip()
