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
* the wall-clock deadline is hard at scheduling boundaries: no new page fetch
  starts after the deadline, politeness/rate-limit waits are bounded by it, and
  in-flight fetches are waited on only up to the deadline (plus a short join
  grace) — threads are never killed unsafely, stragglers are abandoned as
  daemon threads and reported honestly
* the browser-page budget (``max_dynamic_pages``) is enforced atomically: a
  slot is RESERVED under the lock before a dynamic/stealth-capable fetch
  starts, so concurrent workers can never exceed the cap (0 = unlimited)
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


_ALLOWED_STRATEGIES = ("auto", "static", "dynamic", "stealth")
# Strategies that may end up driving a browser (and therefore consume the
# max_dynamic_pages budget).
_BROWSER_CAPABLE = ("auto", "dynamic", "stealth")
# Extra seconds granted to in-flight fetches after the wall-clock deadline
# before they are abandoned (threads are daemons; they are never killed).
_JOIN_GRACE_SECONDS = 5.0


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
    # Per-crawl strategy policy (spec §6): "auto" uses the intelligent router
    # (static → escalate to dynamic/stealth where permitted); "static" stays
    # lightweight; "dynamic"/"stealth" request that class where policy allows.
    strategy: str = "auto"
    allow_fallback: bool = True
    # Cap how many pages in a crawl may escalate to a (costly) browser strategy,
    # so a JS-heavy site cannot silently spin up unbounded Chromium instances.
    max_dynamic_pages: int = 0  # 0 → unlimited (still bounded by max_pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_urls": list(self.start_urls),
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "max_concurrency": self.max_concurrency,
            "per_domain_delay_ms": self.per_domain_delay_ms,
            "same_site_only": self.same_site_only,
            "extra_domains": list(self.extra_domains),
            "strategy": self.strategy,
            "allow_fallback": self.allow_fallback,
            "max_dynamic_pages": self.max_dynamic_pages,
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
    strategy: str = "auto",
    allow_fallback: bool = True,
    per_domain_delay_ms: int = 500,
    max_dynamic_pages: int = 0,
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
    strat = (strategy or "auto").lower().strip()
    if strat not in _ALLOWED_STRATEGIES:
        strat = "auto"
    return CrawlPlan(
        start_urls=clean_urls[:50],
        max_pages=max(1, min(int(requested_pages or 1), int(max_pages_ceiling))),
        max_depth=max(0, min(int(requested_depth or 0), int(max_depth_ceiling))),
        max_concurrency=max(1, min(int(requested_concurrency or 1), int(max_concurrency_ceiling))),
        same_site_only=same_site_only,
        extra_domains=tuple(d.lower().strip(".") for d in (extra_domains or []) if d),
        want_markdown=want_markdown,
        strategy=strat,
        allow_fallback=bool(allow_fallback),
        per_domain_delay_ms=max(0, int(per_domain_delay_ms or 0)),
        max_dynamic_pages=max(0, int(max_dynamic_pages or 0)),
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
        # Set when the crawl must stop (cancellation or deadline); rate-limit
        # waits block on this event so cancellation propagates immediately.
        self._stop = threading.Event()
        self.cancelled = False
        self.timed_out = False
        self._deadline = 0.0  # set at run() start
        self._dynamic_used = 0  # reserved/used browser-strategy slots (locked)
        self.progress = CrawlProgress(max_pages=plan.max_pages, status=CrawlStatus.QUEUED.value)

    # ------------------------------------------------------------------ run
    def run(self) -> dict[str, Any]:
        """Execute the crawl; returns the bounded job result."""
        started = time.monotonic()
        self._deadline = started + max(0.0, float(self._wall_clock))
        frontier: deque[tuple[str, int]] = deque((u, 0) for u in self.plan.start_urls)
        seen: set[str] = set(self.plan.start_urls)
        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        total_bytes = 0

        self.progress.status = CrawlStatus.RUNNING.value
        self._emit("crawl.started", {**self.plan.to_dict()})

        while frontier:
            if self._check_cancel():
                break
            if self._past_deadline():
                self.timed_out = True
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
                # Hard scheduling boundary: no new fetch starts after cancel
                # or deadline. The rate-limit wait itself is deadline-bounded.
                if self._check_cancel():
                    break
                if self._past_deadline():
                    self.timed_out = True
                    break
                if not self._rate_limit(url):
                    # Wait was interrupted by cancellation/deadline.
                    if self._check_cancel():
                        break
                    self.timed_out = self.timed_out or self._past_deadline()
                    break
                with self._lock:
                    self.progress.current_url = url
                thread = threading.Thread(
                    target=self._fetch_one, args=(url, depth, batch_out), daemon=True)
                threads.append(thread)
                thread.start()
            abandoned = self._join_bounded(threads)
            if abandoned:
                self.timed_out = True
                failures.append({
                    "error": f"wall-clock limit reached; {abandoned} in-flight "
                             "fetch(es) abandoned (not killed)",
                    "class": "timeout",
                })

            for url, item in list(batch_out.items()):
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
            if self.timed_out:
                if not any(f.get("class") == "timeout" for f in failures):
                    failures.append({"error": "wall-clock limit reached", "class": "timeout"})
                break

        if self.cancelled:
            status = CrawlStatus.CANCELLED.value
        elif self.timed_out:
            status = CrawlStatus.TIMEOUT.value
        elif self.progress.pages_processed:
            status = CrawlStatus.COMPLETED.value
        else:
            status = CrawlStatus.FAILED.value
        self.progress.status = status
        self.progress.elapsed_ms = int((time.monotonic() - started) * 1000)
        summary = {
            "status": status,
            "cancelled": self.cancelled,
            "timed_out": self.timed_out,
            "pages_processed": self.progress.pages_processed,
            "pages_failed": self.progress.pages_failed,
            "pages_discovered": self.progress.pages_discovered,
            "dynamic_pages_used": self._dynamic_used,
            "elapsed_ms": self.progress.elapsed_ms,
            "results": results[: self.plan.max_pages],
            "failures": failures[:50],
            "plan": self.plan.to_dict(),
        }
        self._emit("crawl.completed", {k: v for k, v in summary.items() if k != "results"})
        return summary

    # ------------------------------------------------------- deadline/cancel
    def _check_cancel(self) -> bool:
        """Poll the cooperative cancel flag; latch it into the stop event."""
        if self.cancelled:
            return True
        try:
            if self._should_cancel():
                self.cancelled = True
                self._stop.set()
                return True
        except Exception:
            pass
        return False

    def _past_deadline(self) -> bool:
        if time.monotonic() >= self._deadline:
            self._stop.set()
            return True
        return False

    def _remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def _join_bounded(self, threads: list[threading.Thread]) -> int:
        """Wait for in-flight fetches, but never past deadline + grace.

        Returns how many threads were still running when the wait ended
        (abandoned — they are daemon threads and are never killed; their late
        writes into the batch dict are simply not read again).
        """
        for thread in threads:
            budget = self._remaining() + _JOIN_GRACE_SECONDS
            thread.join(timeout=max(0.1, budget))
        return sum(1 for t in threads if t.is_alive())

    # -------------------------------------------------------------- internals
    def _fetch_one(self, url: str, depth: int, out: dict[str, Any]) -> None:
        if depth > self.plan.max_depth:
            out[url] = {"depth": depth, "result": "depth limit"}
            return
        if self._stop.is_set():
            # Cancellation/deadline latched between scheduling and start: do
            # not fetch; leave no record so the page is not counted as failed.
            return
        # Per-crawl strategy policy (spec §6): the crawl uses the SAME intelligent
        # router as single-page acquisition instead of forcing static. "auto"
        # starts static and escalates to dynamic/stealth only where the router's
        # policy + capabilities permit, so a JS-heavy page discovered mid-crawl
        # can actually render instead of failing — while lightweight pages stay
        # lightweight. A browser-strategy budget (max_dynamic_pages) prevents a
        # crawl from spinning up unbounded browser instances.
        strategy = self.plan.strategy or "auto"
        allow_fallback = self.plan.allow_fallback
        reserved = False
        if strategy in _BROWSER_CAPABLE and self.plan.max_dynamic_pages:
            # Reserve the browser slot BEFORE the fetch starts (atomic under
            # the lock) so concurrent workers can never exceed the budget.
            reserved = self._reserve_dynamic_slot()
            if not reserved:
                # Browser budget exhausted — keep this page on the cheap path.
                strategy = "static"
                allow_fallback = False
        try:
            result = self._router.fetch(url, strategy=strategy,
                                        want_markdown=self.plan.want_markdown,
                                        allow_fallback=allow_fallback)
            used_browser = getattr(result, "strategy", "static") in ("dynamic", "stealth")
            if reserved and not used_browser:
                # AUTO stayed on the cheap path — return the unused slot.
                self._release_dynamic_slot()
            elif used_browser and not reserved:
                # Unlimited budget (0): still count usage for observability.
                with self._lock:
                    self._dynamic_used += 1
            out[url] = {"depth": depth, "result": result}
        except Exception as exc:
            if reserved:
                self._release_dynamic_slot()
            out[url] = {"depth": depth, "result": f"{type(exc).__name__}: {exc}"}

    def _reserve_dynamic_slot(self) -> bool:
        """Atomically claim one browser-strategy slot; False when exhausted."""
        with self._lock:
            if self.plan.max_dynamic_pages and \
                    self._dynamic_used >= self.plan.max_dynamic_pages:
                return False
            self._dynamic_used += 1
            return True

    def _release_dynamic_slot(self) -> None:
        with self._lock:
            if self._dynamic_used > 0:
                self._dynamic_used -= 1

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

    def _rate_limit(self, url: str) -> bool:
        """Per-domain politeness delay (spec §11), bounded by cancel + deadline.

        Returns True when the caller may fetch; False when the wait was
        interrupted by cancellation or the wall-clock deadline (never sleeps
        past either).
        """
        host = (urlparse(url).hostname or "").lower()
        delay = self.plan.per_domain_delay_ms / 1000.0
        while True:
            if self._stop.is_set() or self._check_cancel():
                return False
            with self._lock:
                last = self._domain_locks.get(host, 0.0)
                now = time.monotonic()
                if now - last >= delay:
                    self._domain_locks[host] = now
                    return True
                wait = delay - (now - last)
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                self._stop.set()
                return False
            # Event-based wait: wakes immediately on cancellation/deadline latch.
            if self._stop.wait(timeout=min(wait, remaining, 2.0)):
                return False

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
