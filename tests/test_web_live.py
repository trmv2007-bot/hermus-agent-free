"""REAL acquisition tests — honestly labeled (spec §25).

Two tiers, never conflated:

* REAL LIVE TEST — an actual outbound request to a real public website
  (pypi.org, safe and stable). Skipped automatically when the runtime has no
  egress to it; when it runs it proves: request left the process, Scrapling
  performed the acquisition, extraction worked, a normalized WebResult came out.
* REAL LOCAL TEST — a real HTTP fetch against a loopback fixture server.
  Exercises the same full stack without internet access.

Nothing in this file is mocked. If a test is skipped, the skip reason says why.
"""
from __future__ import annotations

import socket

import pytest

from core.web import capabilities
from core.web.gateway import WebGateway


class LiveConfig:
    web_enabled = True
    web_default_strategy = "static"
    web_dynamic_enabled = False          # browsers unavailable in CI sandboxes
    web_stealth_enabled = False
    web_termux_restrict = True
    web_request_timeout = 15.0
    web_browser_timeout = 15.0
    web_max_response_bytes = 2_000_000
    web_max_redirects = 5
    web_allow_private_addresses = False  # LIVE: production posture
    web_allowed_domains: list = []
    web_blocked_domains: list = []
    web_crawl_max_pages = 10
    web_crawl_max_depth = 2
    web_crawl_concurrency = 2
    web_crawl_wall_clock = 60.0
    web_max_sessions = 4
    web_session_ttl = 300.0
    web_cache_enabled = True
    web_cache_size = 8
    web_cache_ttl = 60
    web_max_content_chars = 20000


def _pypi_reachable() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=4).close()
        return True
    except OSError:
        return False


LIVE = pytest.mark.skipif(not _pypi_reachable(), reason="no network egress to pypi.org "
                                                           "from this environment")


@pytest.fixture()
def live_gateway():
    from core.events import EventBus

    return WebGateway(LiveConfig(), bus=EventBus())


@pytest.fixture(autouse=True)
def _reset_verification():
    capabilities._cache.clear()
    yield
    capabilities._cache.clear()


@LIVE
class TestRealLiveInternetFetch:
    def test_real_live_fetch_produces_normalized_result(self, live_gateway):
        """REAL LIVE TEST (not a mock): pypi.org/simple/scrapling/."""
        result = live_gateway.fetch("https://pypi.org/simple/scrapling/", use_cache=False)
        assert result.ok is True, f"live fetch failed: {result.error}"
        assert result.status_code == 200
        assert result.source == "scrapling"
        assert result.strategy == "static"
        assert "scrapling" in result.title.lower()
        assert result.sha256 and result.size_bytes > 0
        assert result.links, "real page must yield absolute links"
        assert all(l.url.startswith("http") for l in result.links)
        # capability must now be VERIFIED (a real fetch succeeded in-process)
        assert capabilities.probe(force=True)["static"]["status"] == capabilities.AVAILABLE

    def test_real_live_extraction(self, live_gateway):
        """REAL LIVE TEST: targeted extraction over a real page."""
        out = live_gateway.extract("https://pypi.org/simple/scrapling/", selector="a",
                                   method="css", attribute="href", max_values=5)
        assert out["ok"] is True
        assert out["count"] >= 1
        assert all(v for v in out["values"])

    def test_real_live_ssrf_still_enforced(self, live_gateway):
        """REAL LIVE TEST: with internet available, internal targets are STILL
        refused — live capability never weakens security."""
        result = live_gateway.fetch("http://169.254.169.254/latest/meta-data/")
        assert result.ok is False
        assert result.error_code == "WEB_SECURITY_BLOCKED"


class TestRealLocalFetch:
    """Full stack over loopback (fixture server) — real fetch, no internet."""

    def test_real_local_fetch_extract_and_crawl(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        pages = {
            "/": "<html><head><title>Index</title></head><body><p>Intro page with plenty "
                 "of text for the sufficiency heuristic.</p>"
                 "<a href='/doc'>doc</a><a href='/prices'>prices</a></body></html>",
            "/doc": "<html><head><title>Docs</title></head><body><p>Documentation page "
                    "with enough meaningful text to pass sufficiency checks.</p></body></html>",
            "/prices": "<html><head><title>Prices</title></head><body>"
                       "<ul class='prices'><li class='price'>19.99</li>"
                       "<li class='price'>29.99</li></ul>"
                       "<p>Price list page with enough supporting text as well.</p>"
                       "</body></html>",
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = pages.get(self.path, "<html><body>missing</body></html>").encode()
                self.send_response(200 if self.path in pages else 404)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            from core.events import EventBus

            cfg = LiveConfig()
            cfg.web_allow_private_addresses = True  # fixture site on loopback
            gateway = WebGateway(cfg, bus=EventBus())
            base = f"http://127.0.0.1:{server.server_port}"

            # fetch + extract prices with a CSS selector (real parser work)
            out = gateway.extract(f"{base}/prices", selector="li.price", method="css")
            assert out["ok"] is True
            assert out["values"] == ["19.99", "29.99"]
            assert out["source_url"].endswith("/prices")

            # bounded crawl following real links
            summary = gateway.crawl([f"{base}/"], max_pages=3, max_depth=1, concurrency=2)
            assert summary["status"] == "completed"
            assert summary["pages_processed"] == 3
            fetched = sorted(r["url"] for r in summary["results"])
            assert fetched == [base + "/", base + "/doc", base + "/prices"]
        finally:
            server.shutdown()
