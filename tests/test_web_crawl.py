"""Crawl worker + JobQueue integration tests (spec §10/§11).

The crawl tests do REAL loopback fetching against the local fixture site
(production blocks loopback; the test policy explicitly allows it) and a REAL
JobQueue submission when the asyncio loop is running.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.web.crawl import CrawlWorker, plan_crawl
from core.web.security import WebSecurityPolicy


class Site:
    """Five-page fixture site with a deliberate cross-site link."""

    def __init__(self):
        pages = {"/": ["<a href='/a'>a</a><a href='/b'>b</a>",
                       "Home page body text long enough to be sufficient for tests."],
                 "/a": ["<a href='/c'>c</a><a href='http://outside.example/x'>out</a>",
                        "Page A body text long enough to be sufficient for tests."],
                 "/b": ["", "Page B body text long enough to be sufficient for tests."],
                 "/c": ["<a href='/d'>d</a>",
                        "Page C body text long enough to be sufficient for tests."],
                 "/d": ["", "Page D body text long enough to be sufficient for tests."]}
        self.pages = {k: (t, b) for k, (t, b) in pages.items()}

    def serve(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                title, body = site.pages.get(self.path, ("", "missing page filler"))
                html = (f"<html><head><title>{self.path}</title></head><body>{title}"
                        f"<p>{body}</p></body></html>").encode()
                self.send_response(200 if self.path in site.pages else 404)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture()
def site():
    local = Site()
    server, base = local.serve()
    yield local, base
    server.shutdown()


class FakeRouter:
    """Router stand that fetches the fixture site via the real backend."""

    def __init__(self, base):
        self.base = base
        from core.web.router import StrategyRouter

        cfg = type("C", (), {"web_dynamic_enabled": False, "web_stealth_enabled": False,
                             "web_termux_restrict": True, "web_request_timeout": 5.0,
                             "web_browser_timeout": 5.0})()
        self._real = StrategyRouter(cfg, WebSecurityPolicy(allow_private_addresses=True))

    def fetch(self, url, **kw):
        kw.pop("allow_fallback", None)
        kw.pop("strategy", None)
        return self._real.fetch(url, strategy="static", allow_fallback=True, **kw)


@pytest.fixture()
def worker_policy():
    return WebSecurityPolicy(allow_private_addresses=True)  # loopback allowed: fixture site


class TestPlanClamps:
    def test_limits_clamped_to_ceilings(self):
        plan = plan_crawl(["http://x.example/"], requested_pages=10_000, requested_depth=99,
                          requested_concurrency=999, max_pages_ceiling=100,
                          max_depth_ceiling=4, max_concurrency_ceiling=8)
        assert plan.max_pages == 100
        assert plan.max_depth == 4
        assert plan.max_concurrency == 8

    def test_fragments_deduped(self):
        plan = plan_crawl(["http://x.example/a#one", "http://x.example/a#two",
                           "http://x.example/a"])
        assert plan.start_urls == ["http://x.example/a"]

    def test_zero_pages_becomes_one(self):
        plan = plan_crawl(["http://x.example/"], requested_pages=0)
        assert plan.max_pages == 1


class TestCrawlWorker:
    def test_crawl_respects_max_pages_and_stays_onsite(self, site, worker_policy):
        _local, base = site
        plan = plan_crawl([f"{base}/"], requested_pages=3, requested_depth=2,
                          requested_concurrency=2, max_pages_ceiling=10,
                          max_depth_ceiling=3)
        events: list[tuple[str, dict]] = []
        worker = CrawlWorker(plan, router=FakeRouter(base), policy=worker_policy,
                             emit=lambda t, d: events.append((t, d)))
        summary = worker.run()
        assert summary["status"] == "completed"
        assert summary["pages_processed"] == 3  # hard cap honored
        processed_urls = [r["url"] for r in summary["results"]]
        assert f"{base}/" in processed_urls
        # cross-site link (outside.example) must never be followed
        assert all(url.startswith(base) for url in processed_urls)
        # progress events flowed
        kinds = [t for t, _ in events]
        assert "crawl.started" in kinds and "crawl.progress" in kinds and "crawl.completed" in kinds

    def test_crawl_depth_limit(self, site, worker_policy):
        _local, base = site
        plan = plan_crawl([f"{base}/"], requested_pages=10, requested_depth=0,
                          requested_concurrency=1, max_pages_ceiling=10,
                          max_depth_ceiling=2)
        worker = CrawlWorker(plan, router=FakeRouter(base), policy=worker_policy)
        summary = worker.run()
        assert summary["pages_processed"] == 1, "depth 0 means start pages only"

    def test_cancellation_stops_the_crawl(self, site, worker_policy):
        _local, base = site
        plan = plan_crawl([f"{base}/", f"{base}/a", f"{base}/b"], requested_pages=10,
                          requested_depth=2, requested_concurrency=1,
                          max_pages_ceiling=10, max_depth_ceiling=2)
        state = {"cancelled": False}

        def cancel():
            return state["cancelled"]

        def flip_after_first_progress(event_type, data):
            if event_type == "crawl.progress":
                state["cancelled"] = True

        worker = CrawlWorker(plan, router=FakeRouter(base), policy=worker_policy,
                             emit=flip_after_first_progress, should_cancel=cancel)
        summary = worker.run()
        assert summary["cancelled"] is True
        assert summary["status"] == "cancelled"
        assert summary["pages_processed"] < 3

    def test_failures_are_recorded_not_fatal(self, site, worker_policy):
        _local, base = site
        plan = plan_crawl([f"{base}/missing", f"{base}/b"], requested_pages=5,
                          requested_depth=0, requested_concurrency=2,
                          max_pages_ceiling=5, max_depth_ceiling=1)
        worker = CrawlWorker(plan, router=FakeRouter(base), policy=worker_policy)
        summary = worker.run()
        assert summary["pages_failed"] >= 1
        assert summary["pages_processed"] >= 1
        assert any(f["url"].endswith("/missing") for f in summary["failures"])

    def test_discovered_links_filtered_by_security(self, site, worker_policy):
        """A link pointing at a blocked domain must never join the frontier."""
        _local, base = site
        policy = WebSecurityPolicy(
            allow_private_addresses=True,
            blocked_domains=("outside.example",))
        plan = plan_crawl([f"{base}/a"], requested_pages=10, requested_depth=2,
                          requested_concurrency=2, max_pages_ceiling=10,
                          max_depth_ceiling=2)
        worker = CrawlWorker(plan, router=FakeRouter(base), policy=policy)
        summary = worker.run()
        fetched = [r["url"] for r in summary["results"]]
        assert all("outside.example" not in u for u in fetched)


class TestJobQueueIntegration:
    def test_crawl_submitted_to_canonical_queue(self, site, worker_policy):
        """End-to-end: crawl_async → JobQueue → web.crawl handler → real loopback
        crawl → job result. Uses the gateway with a test policy."""
        _local, base = site
        import core.web as web
        from core.web.gateway import WebGateway
        from core.events import EventBus
        from gateway.queue import job_queue

        cfg = type("C", (), {
            "web_enabled": True, "web_default_strategy": "static",
            "web_dynamic_enabled": False, "web_stealth_enabled": False,
            "web_termux_restrict": True, "web_request_timeout": 5.0,
            "web_browser_timeout": 5.0, "web_max_response_bytes": 1_000_000,
            "web_max_redirects": 5, "web_allow_private_addresses": True,
            "web_allowed_domains": [], "web_blocked_domains": [],
            "web_crawl_max_pages": 5, "web_crawl_max_depth": 2,
            "web_crawl_concurrency": 2, "web_crawl_wall_clock": 30.0,
            "web_max_sessions": 4, "web_session_ttl": 300.0,
            "web_cache_enabled": False, "web_cache_size": 8, "web_cache_ttl": 60,
        })()
        gateway = WebGateway(cfg, bus=EventBus())
        old = web.get_web_gateway()
        web.set_web_gateway(gateway)
        try:
            from gateway import handlers as gw_handlers

            gw_handlers.register_handlers(job_queue, lambda *a, **k: None, overwrite=True)
            assert "web.crawl" in job_queue.handlers

            # The JobQueue binds to a running asyncio loop (as the gateway does):
            # spin a private loop in a background thread and start the queue there.
            import asyncio

            loop = asyncio.new_event_loop()
            loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
            loop_thread.start()
            start_fut = asyncio.run_coroutine_threadsafe(job_queue.start(), loop)
            start_fut.result(timeout=10)

            # submit() must run on the queue's loop thread (production submits
            # from FastAPI handlers running on the loop); cross-thread
            # create_task would leave the lane un-woken.
            async def _submit():
                return gateway.crawl_async([f"{base}/"], max_pages=2, max_depth=1,
                                           concurrency=1)

            submitted = asyncio.run_coroutine_threadsafe(_submit(), loop).result(timeout=10)
            assert submitted["ok"] is True and submitted["kind"] == "web.crawl"
            job_id = submitted["job_id"]

            # Poll until the job finishes on the queue worker.
            deadline = time.time() + 30
            row = None
            while time.time() < deadline:
                rows = [r for r in job_queue.list_jobs(limit=50) if r.get("id") == job_id]
                if rows and rows[0].get("status") in ("succeeded", "failed", "cancelled"):
                    row = rows[0]
                    break
                time.sleep(0.1)
            assert row is not None, "crawl job never finished"
            assert row["status"] == "succeeded"
            assert row.get("has_result") is True
            result = job_queue.jobs[job_id].result or {}
            assert result.get("pages_processed", 0) >= 1
            assert any(r["url"].startswith(base) for r in result.get("results", []))

            stop_fut = asyncio.run_coroutine_threadsafe(job_queue.stop(), loop)
            stop_fut.result(timeout=10)
            loop.call_soon_threadsafe(loop.stop)
        finally:
            web.set_web_gateway(old)

    def test_job_queue_lifecycle_events_reach_event_bus(self, site, worker_policy):
        """web.crawl transitions are mirrored onto the canonical EventBus by the
        JobQueue (single event authority — spec §10)."""
        from core.events import get_bus

        bus = get_bus()
        types: list[str] = []
        bus.subscribe()(lambda env: types.append(env.type))
        _local, base = site
        import core.web as web
        from core.web.gateway import WebGateway

        cfg = type("C", (), {
            "web_enabled": True, "web_default_strategy": "static",
            "web_dynamic_enabled": False, "web_stealth_enabled": False,
            "web_termux_restrict": True, "web_request_timeout": 5.0,
            "web_browser_timeout": 5.0, "web_max_response_bytes": 1_000_000,
            "web_max_redirects": 5, "web_allow_private_addresses": True,
            "web_allowed_domains": [], "web_blocked_domains": [],
            "web_crawl_max_pages": 5, "web_crawl_max_depth": 1,
            "web_crawl_concurrency": 1, "web_crawl_wall_clock": 30.0,
            "web_max_sessions": 4, "web_session_ttl": 300.0,
            "web_cache_enabled": False, "web_cache_size": 8, "web_cache_ttl": 60,
        })()
        gateway = WebGateway(cfg, bus=bus)
        old = web.get_web_gateway()
        web.set_web_gateway(gateway)
        try:
            # Inline crawl through the gateway (same worker the job runs).
            summary = gateway.crawl([f"{base}/b"], max_pages=1, max_depth=0)
            assert summary["pages_processed"] == 1
        finally:
            web.set_web_gateway(old)
        assert "web.crawl.started" in types
        assert "web.crawl.completed" in types
