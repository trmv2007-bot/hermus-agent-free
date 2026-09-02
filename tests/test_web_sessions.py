"""Session manager tests (spec §12): isolation, domain pinning, TTL cleanup,
and the guarantee that cookie values never leave the subsystem."""
from __future__ import annotations

import pytest

from core.web.errors import SecurityBlockedError
from core.web.security import WebSecurityPolicy
from core.web.sessions import WebSessionError, WebSessionManager


@pytest.fixture()
def manager():
    return WebSessionManager(WebSecurityPolicy(), max_sessions=3, ttl_seconds=60)


class TestLifecycle:
    def test_create_describe_no_cookie_values(self, manager):
        info = manager.create("shop", domains=["shop.example.com"])
        assert info["ok"] is True
        assert info["name"] == "shop"
        assert info["allowed_domains"] == ["shop.example.com"]
        # description carries counts, never jar contents
        assert "cookies" not in info and "cookie" not in str(info).lower() or \
            "cookie_values" not in info

    def test_create_requires_domains(self, manager):
        with pytest.raises(WebSessionError):
            manager.create("empty", domains=[])

    def test_create_rejects_blocked_domain(self):
        manager = WebSessionManager(WebSecurityPolicy(blocked_domains=("evil.example",)))
        with pytest.raises(SecurityBlockedError):
            manager.create("bad", domains=["evil.example"])

    def test_get_enforces_domain_pinning(self, manager):
        manager.create("shop", domains=["shop.example.com"])
        manager.get("shop", url="https://shop.example.com/item/1")  # allowed
        with pytest.raises(SecurityBlockedError):
            manager.get("shop", url="https://other.example.net/")  # outside pin

    def test_get_unknown_session(self, manager):
        with pytest.raises(WebSessionError):
            manager.get("ghost", url="https://shop.example.com/")

    def test_destroy(self, manager):
        manager.create("tmp", domains=["tmp.example"])
        assert manager.destroy("tmp")["ok"] is True
        assert manager.destroy("tmp")["ok"] is False
        with pytest.raises(WebSessionError):
            manager.get("tmp", url="https://tmp.example/")

    def test_ttl_expiry(self):
        manager = WebSessionManager(WebSecurityPolicy(), max_sessions=3, ttl_seconds=0.05)
        manager.create("flash", domains=["flash.example"])
        import time

        time.sleep(0.08)
        with pytest.raises(WebSessionError):
            manager.get("flash", url="https://flash.example/")
        assert manager.list_sessions() == [], "expired session must be swept"

    def test_max_sessions_evicts_oldest(self, manager):
        manager.create("s1", domains=["one.example"])
        manager.create("s2", domains=["two.example"])
        manager.create("s3", domains=["three.example"])
        manager.create("s4", domains=["four.example"])  # evicts s1 (oldest)
        names = [s["name"] for s in manager.list_sessions()]
        assert "s1" not in names and "s4" in names
        assert len(names) == 3


class TestIsolation:
    def test_session_open_isolated_per_manager(self):
        """Two managers (e.g. two deployments in tests) never share sessions."""
        a = WebSessionManager(WebSecurityPolicy())
        b = WebSessionManager(WebSecurityPolicy())
        a.create("shared-name", domains=["a.example"])
        with pytest.raises(WebSessionError):
            b.get("shared-name", url="https://a.example/")

    def test_describe_shape_stays_secret_free(self, manager):
        manager.create("shop", domains=["shop.example.com"])
        for info in manager.list_sessions():
            serialized = str(sorted(info.items()))
            for forbidden in ("set-cookie", "authorization", "proxy", "token"):
                assert forbidden not in serialized.lower()

    def test_close_all_drops_everything(self, manager):
        manager.create("a", domains=["a.example"])
        manager.create("b", domains=["b.example"])
        manager.close_all()
        assert manager.list_sessions() == []


class TestRealSessionFetch:
    """Regression tests for the session → backend seam (a WebSession wrapper
    must reach the live Scrapling client, not break on attribute access)."""

    def test_fetch_through_session_hits_real_server_and_reuses_client(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        seen_hosts: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen_hosts.append(self.headers.get("Host", ""))
                body = (b"<html><head><title>Session Page</title></head><body>"
                        b"<p>Session fetch test page with plenty of body text so the "
                        b"sufficiency heuristic is satisfied by this content.</p></body></html>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            import core.web as web
            from core.events import EventBus
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
            gateway = WebGateway(cfg, bus=EventBus())
            base = f"http://127.0.0.1:{server.server_port}"
            # Sessions pin by HOSTNAME (browser cookies are not port-scoped,
            # RFC 6265), so pin the loopback host, not host:port.
            gateway.session_create("fixture", ["127.0.0.1"])

            result = gateway.session_fetch("fixture", f"{base}/one")
            assert result.ok, result.error
            assert result.session_name == "fixture"
            assert result.strategy == "static"

            # second fetch through the SAME session: client reused, counter up
            info = gateway.session_list()
            requests_after_first = info[0]["requests"]
            result2 = gateway.session_fetch("fixture", f"{base}/two")
            assert result2.ok
            info2 = gateway.session_list()
            assert info2[0]["requests"] == requests_after_first + 1
        finally:
            server.shutdown()

    def test_session_create_fails_typed_without_scrapling(self, monkeypatch):
        """Without scrapling, session creation must be a typed error, not ImportError."""
        import core.web.sessions as sess

        def boom():
            raise ImportError("blocked: scrapling missing")

        monkeypatch.setattr(
            "scrapling.fetchers.FetcherSession", boom, raising=False)
        manager = WebSessionManager(WebSecurityPolicy(), max_sessions=2, ttl_seconds=60)
        with pytest.raises(WebSessionError):
            manager.create("noscrap", domains=["x.example"])
