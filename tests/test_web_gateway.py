"""WebGateway tests: security gates, normalization, caching, and a REAL
end-to-end fetch against a local HTTP server (loopback permitted explicitly by
the test-only policy — production policy blocks it by default)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.web.gateway import WebGateway
from core.web.security import (
    SecurityBlockedError,
    WebSecurityPolicy,
    redact_url,
    host_matches_pattern,
)


# --------------------------------------------------------------------------- fakes
class StubConfig:
    """Minimal config stand — WebGateway reads web_* via getattr with defaults."""

    web_enabled = True
    web_default_strategy = "static"
    web_dynamic_enabled = True
    web_stealth_enabled = False
    web_termux_restrict = True
    web_request_timeout = 5.0
    web_browser_timeout = 5.0
    web_max_response_bytes = 1_000_000
    web_max_redirects = 5
    web_allow_private_addresses = True          # test-only: local HTTP server
    web_allowed_domains: list = []
    web_blocked_domains: list = []
    web_crawl_max_pages = 20
    web_crawl_max_depth = 2
    web_crawl_concurrency = 4
    web_crawl_wall_clock = 30.0
    web_max_sessions = 4
    web_session_ttl = 300.0
    web_cache_enabled = True
    web_cache_size = 16
    web_cache_ttl = 60


class LocalSite:
    """A tiny fixture web server with deterministic pages."""

    def __init__(self):
        self.pages = {
            "/": ("text/html", "<html><head><title>Home</title></head><body>"
                               "<h1>Hello Hermus</h1><p>Lots of meaningful body text "
                               "so the router deems this page sufficient for the test.</p>"
                               "<a href='/page2'>next</a><a href='http://evil.exampleOutside/x'>out</a>"
                               "</body></html>"),
            "/page2": ("text/html", "<html><head><title>Page Two</title></head><body>"
                                    "<p>Second page with enough real text to pass the "
                                    "sufficiency heuristic used by the strategy router.</p></body></html>"),
            "/js-shell": ("text/html", "<html><head><title>App</title></head><body>"
                                       "<div id='root'></div><noscript>Enable JavaScript</noscript>"
                                       "</body></html>"),
            "/data.json": ("application/json", json.dumps({"price": 42, "ok": True})),
            "/big": ("application/octet-stream", "x" * 300),
        }
        self.hit_log: list[str] = []

    def serve(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                site.hit_log.append(self.path)
                ctype, body = site.pages.get(self.path) or (
                    "text/html", "<html><head><title>404</title></head><body>not found "
                                 "page with filler text</body></html>")
                status = 200 if self.path in site.pages else 404
                payload = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture()
def site():
    local = LocalSite()
    server, base = local.serve()
    yield local, base
    server.shutdown()


@pytest.fixture()
def gateway():
    from core.events import EventBus

    return WebGateway(StubConfig(), bus=EventBus())


# --------------------------------------------------------------------------- security
class TestSecurityGate:
    def test_loopback_blocked_by_default_policy(self):
        policy = WebSecurityPolicy()  # production default: private addresses refused
        with pytest.raises(SecurityBlockedError):
            policy.check("http://127.0.0.1:8000/admin")

    def test_metadata_ip_blocked(self):
        policy = WebSecurityPolicy(allow_private_addresses=False)
        with pytest.raises(SecurityBlockedError):
            policy.check("http://169.254.169.254/latest/meta-data/")

    def test_unsafe_schemes_rejected(self):
        policy = WebSecurityPolicy(allow_private_addresses=True)
        for url in ("file:///etc/passwd", "ftp://example.com/x", "data:text/html,hi",
                    "gopher://example.com"):
            with pytest.raises(SecurityBlockedError):
                policy.check(url)

    def test_embedded_credentials_rejected(self):
        policy = WebSecurityPolicy(allow_private_addresses=True)
        with pytest.raises(SecurityBlockedError):
            policy.check("http://user:secret@example.com/")

    def test_blocked_port_rejected(self):
        policy = WebSecurityPolicy(allow_private_addresses=True)
        with pytest.raises(SecurityBlockedError):
            policy.check("http://example.com:6379/")

    def test_local_and_internal_hosts_rejected(self):
        policy = WebSecurityPolicy(allow_private_addresses=True)
        for url in ("http://localhost/x", "http://api.internal/x", "http://box.local/"):
            with pytest.raises(SecurityBlockedError):
                policy.check(url)

    def test_domain_block_list_wins(self):
        policy = WebSecurityPolicy(blocked_domains=("evil.example", "*.tracker.example"))
        with pytest.raises(SecurityBlockedError):
            policy.check("https://evil.example/page")
        with pytest.raises(SecurityBlockedError):
            policy.check("https://sub.tracker.example/pixel.gif")

    def test_allow_list_restricts_everything_else(self):
        policy = WebSecurityPolicy(allowed_domains=("docs.example.com",))
        policy.check("https://docs.example.com/guide")
        with pytest.raises(SecurityBlockedError):
            policy.check("https://other.example.net/")

    def test_host_matches_pattern_forms(self):
        assert host_matches_pattern("evil.example", "evil.example")
        assert host_matches_pattern("sub.evil.example", "*.evil.example")
        assert host_matches_pattern("sub.evil.example", "evil.example")
        assert not host_matches_pattern("notevil.example", "evil.example")
        assert not host_matches_pattern("evil.example", "*.evil.example")

    def test_redirect_to_private_rejected(self):
        policy = WebSecurityPolicy()
        with pytest.raises(SecurityBlockedError):
            policy.check_final_url("http://127.0.0.1:9000/", purpose="redirect")

    def test_gateway_returns_typed_security_result_not_exception(self, site):
        """A security refusal (unsafe scheme) becomes a typed failed WebResult —
        the tool surface never sees a raw exception."""
        from core.events import EventBus

        _local, base = site
        gateway = WebGateway(StubConfig(), bus=EventBus())
        result = gateway.fetch("file:///etc/passwd")
        assert result.ok is False
        assert result.error_code == "WEB_SECURITY_BLOCKED"
        assert result.failure_class == "security_blocked"

    def test_redaction_strips_query_and_credentials(self):
        red = redact_url("http://user:pw@host.example/p?a=tok&b=2")
        assert "tok" not in red and "pw" not in red and "?" not in red
        assert red == "http://host.example/p"


# --------------------------------------------------------------------------- fetch (real loopback)
class TestGatewayFetchRealLocal:
    def test_real_fetch_and_normalization(self, gateway, site):
        _local, base = site
        result = gateway.fetch(f"{base}/")
        assert result.ok is True
        assert result.status_code == 200
        assert result.title == "Home"
        assert "Hello Hermus" in result.text
        assert result.strategy == "static"
        assert result.sha256 and result.size_bytes > 0
        assert result.untrusted is True
        # cookies / request headers must never appear in a WebResult
        assert not hasattr(result, "cookies")
        assert not hasattr(result, "request_headers")
        # links are absolutized; external link kept, anchor filtered
        urls = [l.url for l in result.links]
        assert f"{base}/page2" in urls

    def test_cache_hit_within_ttl(self, gateway, site):
        _local, base = site
        first = gateway.fetch(f"{base}/page2")
        second = gateway.fetch(f"{base}/page2")
        assert first.ok and second.ok
        assert second.cached is True
        assert first.cached is False

    def test_cache_key_includes_html_shape(self, gateway, site):
        """Regression: a no-HTML cached result must never satisfy an
        extraction fetch (which needs html to run selectors)."""
        _local, base = site
        plain = gateway.fetch(f"{base}/", include_html=False, use_cache=True)
        assert plain.ok and plain.html is None
        extractable = gateway.fetch(f"{base}/", include_html=True, use_cache=True)
        assert extractable.ok
        assert extractable.html, "extraction fetch must not be served the html-less cache entry"

    def test_cache_disabled(self, site):
        from core.events import EventBus

        cfg = StubConfig()
        cfg.web_cache_enabled = False
        gateway = WebGateway(cfg, bus=EventBus())
        _local, base = site
        gateway.fetch(f"{base}/")
        again = gateway.fetch(f"{base}/")
        assert again.cached is False

    def test_404_is_a_typed_failure_with_status(self, gateway, site):
        _local, base = site
        result = gateway.fetch(f"{base}/missing")
        assert result.ok is False  # a 404 is a real failure, reported honestly
        assert result.status_code == 404
        assert result.failure_class == "http_status"
        assert any(a.outcome == "insufficient" for a in result.attempts)

    def test_fetch_text_labels_and_bounds(self, gateway, site):
        _local, base = site
        read = gateway.fetch_text(f"{base}/", max_chars=50)
        assert read["ok"] is True
        assert read["untrusted"] is True
        assert read["truncated"] is True
        assert len(read["content"]) <= 50

    def test_disabled_subsystem_typed_result(self, site):
        from core.events import EventBus

        cfg = StubConfig()
        cfg.web_enabled = False
        gateway = WebGateway(cfg, bus=EventBus())
        _local, base = site
        result = gateway.fetch(f"{base}/")
        assert result.ok is False
        assert result.error_code == "WEB_DISABLED"

    def test_telemetry_emitted_onto_canonical_bus(self, site):
        from core.events import EventBus

        bus = EventBus()
        seen: list[str] = []
        bus.subscribe()(lambda env: seen.append(env.type))
        gateway = WebGateway(StubConfig(), bus=bus)
        _local, base = site
        gateway.fetch(f"{base}/")
        assert "web.fetch" in seen

    def test_telemetry_redacts_urls(self, site):
        from core.events import EventBus

        bus = EventBus()
        targets: list[str] = []
        bus.subscribe()(lambda env: targets.append(env.target or ""))
        gateway = WebGateway(StubConfig(), bus=bus)
        _local, base = site
        gateway.fetch(f"{base}/?token=supersecret")
        assert "web.fetch" in targets or any(t for t in targets)  # event captured
        assert all("supersecret" not in t for t in targets), targets


# --------------------------------------------------------------------------- normalization
class TestNormalization:
    def test_secret_fields_never_present(self, gateway, site):
        _local, base = site
        result = gateway.fetch(f"{base}/")
        d = result.to_dict()
        for key in ("cookies", "headers", "request_headers", "proxy"):
            assert key not in d

    def test_to_dict_bounds_links_and_text(self, gateway, site):
        _local, base = site
        result = gateway.fetch(f"{base}/")
        d = result.to_dict(max_links=1)
        assert len(d["links"]) == 1
        assert d["links_total"] >= 1

    def test_summary_labels_untrusted(self, gateway, site):
        _local, base = site
        result = gateway.fetch(f"{base}/")
        s = result.summary(max_chars=80)
        assert "untrusted" in s.lower()

    def test_json_content_type_metadata(self, gateway, site):
        _local, base = site
        result = gateway.fetch(f"{base}/data.json")
        assert result.content_type == "application/json"
        assert result.title == ""  # JSON has no title; must not fabricate one
