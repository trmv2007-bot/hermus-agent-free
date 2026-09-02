"""Bounded crawling on top of Scrapling acquisition (spec §10/§11).

Design decision (documented honestly in docs/WEB_ACQUISITION.md): Scrapling's
own ``Spider`` framework runs its private asyncio engine and fetches before
Hermus sees a URL. That conflicts with two Hermus invariants — per-URL SSRF
validation *before* any request, and cooperative cancellation at page
boundaries inside the canonical JobQueue. So the traversal here is Hermus-owned
(frontier, depth, limits, cancellation) while every page fetch itself goes
through Scrapling fetchers/sessions via the backend. One acquisition engine,
Hermus-controlled blast radius.

Guarantees:
* hard caps: max_pages, max_depth, wall-clock, response bytes (per page + total)
* same-site-only traversal unless extra domains are explicitly configured
* per-domain rate limiting (min delay between hits to one host)
* cooperative cancellation checked before every page
* progress events after every page (JobQueue ctx.emit + canonical EventBus)
* per-page results are normalized WebResults (bounded), failures recorded
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urldefrag, urlparse

from .models import CrawlProgress, CrawlStatus, WebResult
from .normalizer import same_site
from .security import WebSecurityPolicy

EmitFn = Callable[[str, dict[str, Any]], None]
CancelFn = Callable[[], bool]


@dataclass
class CrawlPlan:
    """Validated crawl parameters (clamped to configured ceilings)."""

    start_urls: list[str] = field(default_factory=list)
    max_pages: int = 10
    max_depth: int = 2
    max_concurrency: int = 2
    per_domain_delay_ms: int = 500
    same_site_only: bool = True
    extra_domains: tuple[str, ...] = ()
    want_markdown: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_urls": list(self.start_urls),
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "max_concurrency": self.max_concurrency,
            "per_domain_delay_ms": self.per_domain_delay_ms,
            "same_site_only": self.same_site_only,
            "extra_domains": list(self.extra_domains),
        }


def plan_crawl(
    urls: list[str],
    *,
    requested_pages: int = 10,
    requested_depth: int = 2,
    requested_concurrency: int = 2,
    max_pages_ceiling: int = 100,
    max_depth_ceiling: int = 4,
    max_concurrency_ceiling: int = 8,
    same_site_only: bool = True,
    extra_domains: Optional[list[str]] = None,
    want_markdown: bool = False,
) -> CrawlPlan:
    """Clamp a crawl request to Hermus's configured ceilings (spec §11)."""
    clean_urls: list[str] = []
    seen: set[str] = set()
    for url in urls or []:
        # Strip fragments: /a#x and /a are the same page to a crawler.
        bare = urldefrag((url or "").strip())[0]
        if bare and bare not in seen:
            seen.add(bare)
            clean_urls.append(bare)
    return CrawlPlan(
        start_urls=clean_urls[:50],
        max_pages=max(1, min(int(requested_pages or 1), int(max_pages_ceiling))),
        max_depth=max(0, min(int(requested_depth or 0), int(max_depth_ceiling))),
        max_concurrency=max(1, min(int(requested_concurrency or 1), int(max_concurrency_ceiling))),
        same_site_only=same_site_only,
        extra_domains=tuple(d.lower().strip(".") for d in (extra_domains or []) if d),
        want_markdown=want_markdown,
    )


class CrawlWorker:
    """Executes one validated crawl plan. Sync by design (runs on a JobQueue
    worker thread); concurrency comes from a bounded thread pool."""

    def __init__(self, plan: CrawlPlan, *, router: Any, policy: WebSecurityPolicy,
                 emit: Optional[EmitFn] = None, should_cancel: Optional[CancelFn] = None,
                 wall_clock_seconds: float = 600.0):
        self.plan = plan
        self._router = router
        self._policy = policy
        self._emit = emit or (lambda *_: None)
        self._should_cancel = should_cancel or (lambda: False)
        self._wall_clock = wall_clock_seconds
        self._domain_locks: dict[str, float] = {}
        self._lock = threading.Lock()
        self.cancelled = False
        self.progress = CrawlProgress(max_pages=plan.max_pages, status=CrawlStatus.QUEUED.value)

    # ------------------------------------------------------------------ run
    def run(self) -> dict[str, Any]:
        """Execute the crawl; returns the bounded job result."""
        started = time.monotonic()
        frontier: deque[tuple[str, int]] = deque((u, 0) for u in self.plan.start_urls)
        seen: set[str] = set(self.plan.start_urls)
        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        total_bytes = 0

        self.progress.status = CrawlStatus.RUNNING.value
        self._emit("crawl.started", {**self.plan.to_dict()})

        while frontier:
            if self._should_cancel():
                self.cancelled = True
                break
            if time.monotonic() - started > self._wall_clock:
                failures.append({"error": "wall-clock limit reached", "class": "timeout"})
                break
            if self.progress.pages_processed >= self.plan.max_pages:
                break
            if total_bytes >= self._policy.max_response_bytes:
                failures.append({"error": "total crawl size limit reached", "class": "size_limit"})
                break

            batch: list[tuple[str, int]] = []
            while frontier and len(batch) < self.plan.max_concurrency:
                batch.append(frontier.popleft())
            threads: list[threading.Thread] = []
            batch_out: dict[str, Any] = {}

            for url, depth in batch:
                self._rate_limit(url)
                if self._should_cancel():
                    self.cancelled = True
                    break
                with self._lock:
                    self.progress.current_url = url
                thread = threading.Thread(
                    target=self._fetch_one, args=(url, depth, batch_out), daemon=True)
                threads.append(thread)
                thread.start()
            for thread in threads:
                thread.join()

            for url, item in batch_out.items():
                depth = item["depth"] if isinstance(item, dict) and "depth" in item else 0
                result = item.get("result") if isinstance(item, dict) else item
                if isinstance(result, WebResult):
                    total_bytes += min(result.size_bytes, self._policy.max_response_bytes)
                    if result.ok:
                        self.progress.pages_processed += 1
                        results.append(self._page_record(result))
                        if depth < self.plan.max_depth:
                            for link in result.links:
                                if len(seen) >= self.plan.max_pages * 10:
                                    break  # discovery is bounded too
                                nxt = link.url
                                if nxt in seen:
                                    continue
                                if not self._link_allowed(nxt):
                                    continue
                                seen.add(nxt)
                                self.progress.pages_discovered += 1
                                frontier.append((nxt, depth + 1))
                    else:
                        self.progress.pages_failed += 1
                        failures.append({
                            "url": url, "error": result.error[:200],
                            "class": result.failure_class,
                        })
                else:
                    self.progress.pages_failed += 1
                    failures.append({"url": url, "error": str(result)[:200],
                                     "class": "fetch_error"})

            # Enqueue discovered links with correct depth (from the page they came from).
            self._emit("crawl.progress", self._progress_dict(started))
            if self.progress.pages_processed >= self.plan.max_pages:
                break

        status = (CrawlStatus.CANCELLED.value if self.cancelled else CrawlStatus.COMPLETED.value) \
            if self.progress.pages_processed or self.cancelled else CrawlStatus.FAILED.value
        self.progress.status = status
        self.progress.elapsed_ms = int((time.monotonic() - started) * 1000)
        summary = {
            "status": status,
            "cancelled": self.cancelled,
            "pages_processed": self.progress.pages_processed,
            "pages_failed": self.progress.pages_failed,
            "pages_discovered": self.progress.pages_discovered,
            "elapsed_ms": self.progress.elapsed_ms,
            "results": results[: self.plan.max_pages],
            "failures": failures[:50],
            "plan": self.plan.to_dict(),
        }
        self._emit("crawl.completed", {k: v for k, v in summary.items() if k != "results"})
        return summary

    # -------------------------------------------------------------- internals
    def _fetch_one(self, url: str, depth: int, out: dict[str, Any]) -> None:
        if depth > self.plan.max_depth:
            out[url] = {"depth": depth, "result": "depth limit"}
            return
        try:
            result = self._router.fetch(url, strategy="static",
                                        want_markdown=self.plan.want_markdown,
                                        allow_fallback=False)
            out[url] = {"depth": depth, "result": result}
        except Exception as exc:
            out[url] = {"depth": depth, "result": f"{type(exc).__name__}: {exc}"}

    def _link_allowed(self, url: str) -> bool:
        """Security + scope check before a discovered link joins the frontier."""
        try:
            self._policy.check(url, purpose="crawl-link")
        except Exception:
            return False
        if self.plan.same_site_only:
            anchors = [u for u in self.plan.start_urls]
            if not any(same_site(url, anchor) for anchor in anchors) \
                    and not self._extra_domain(url):
                return False
        return True

    def _extra_domain(self, url: str) -> bool:
        from .security import host_matches_pattern

        host = (urlparse(url).hostname or "").lower().strip(".")
        return any(host_matches_pattern(host, d) for d in self.plan.extra_domains)

    def _rate_limit(self, url: str) -> None:
        """Per-domain politeness delay (spec §11)."""
        host = (urlparse(url).hostname or "").lower()
        delay = self.plan.per_domain_delay_ms / 1000.0
        while True:
            with self._lock:
                last = self._domain_locks.get(host, 0.0)
                now = time.monotonic()
                if now - last >= delay:
                    self._domain_locks[host] = now
                    return
                wait = delay - (now - last)
            time.sleep(min(wait, 2.0))

    def _progress_dict(self, started: float) -> dict[str, Any]:
        self.progress.elapsed_ms = int((time.monotonic() - started) * 1000)
        return self.progress.to_dict()

    @staticmethod
    def _page_record(result: WebResult) -> dict[str, Any]:
        return {
            "url": result.final_url or result.url,
            "status_code": result.status_code,
            "title": result.title[:300],
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "strategy": result.strategy,
            "text_chars": len(result.text),
            "links_found": len(result.links),
            "text": result.text[:2000],
        }
