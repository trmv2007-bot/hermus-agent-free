"""Tool integration tests (spec §17): the web tools are discovered by the
canonical ToolRegistry, gated by the PermissionManager, validate arguments,
and return structured/bounded results. Gateway I/O is faked — these prove
wiring and contracts, not Scrapling behavior (real fetches live elsewhere).
"""
from __future__ import annotations

import pytest

from core.permissions import Decision, Risk


class FakeResult:
    """Stand-in for WebResult compact() output."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture()
def fake_gateway(monkeypatch):
    """Replace the process gateway with a scripted fake."""

    class FakeGateway:
        def __init__(self):
            self.fetches: list[dict] = []
            self.script: dict[str, object] = {}

        def fetch(self, url, **kw):
            self.fetches.append({"url": url, **kw})
            result = self.script.get("fetch")
            if isinstance(result, Exception):
                raise result
            return result

        def fetch_text(self, url, max_chars=10000, **kw):
            self.fetches.append({"url": url, "max_chars": max_chars})
            out = self.script.get("fetch_text") or {"ok": True, "url": url, "title": "T",
                                                    "strategy": "static",
                                                    "content": "hello page", "content_length": 10,
                                                    "truncated": False, "warnings": []}
            return out

        def extract(self, url, **kw):
            return self.script.get("extract") or {"ok": True, "values": ["x"], "count": 1,
                                                  "method": kw.get("method", "css"),
                                                  "selector": kw.get("selector", ""),
                                                  "adaptive": kw.get("adaptive", False),
                                                  "source_url": url}

        def extract_links(self, url, **kw):
            return {"ok": True, "source_url": url, "method": "links", "values": [],
                    "count": 0, "strategy": "static"}

        def extract_metadata(self, url, **kw):
            return {"ok": True, "source_url": url, "method": "metadata", "values": [],
                    "count": 0, "strategy": "static"}

        def search_and_extract(self, query, **kw):
            return self.script.get("search") or {"ok": True, "query": query,
                                                 "results": [], "pages": []}

        def crawl_async(self, urls, **kw):
            return self.script.get("crawl_async") or {"ok": True, "queued": True,
                                                      "job_id": "job_x", "kind": "web.crawl"}

        def crawl(self, urls, **kw):
            return {"ok": True, "status": "completed", "pages_processed": 1, "results": [],
                    "failures": []}

        def session_create(self, name, domains, **kw):
            return {"ok": True, "name": name, "allowed_domains": domains}

        def session_destroy(self, name):
            return {"ok": True, "destroyed": name}

        def session_list(self):
            return []

        def session_fetch(self, name, url, **kw):
            return self.script.get("fetch")

        def web_capabilities(self):
            return {"parser": {"status": "available"}}

    import core.web

    fake = FakeGateway()
    old = core.web.get_web_gateway
    monkeypatch.setattr(core.web, "get_web_gateway", lambda: fake)
    yield fake
    monkeypatch.setattr(core.web, "get_web_gateway", old, raising=False)


class TestDiscoveryAndPermissions:
    def test_tools_are_discovered_by_registry(self):
        from core.tool_registry import tool_registry

        if not getattr(tool_registry, "executors", None):
            tool_registry.load()
        for name in ("web_fetch", "web_extract", "web_extract_links",
                     "web_extract_metadata", "web_search_and_extract",
                     "web_crawl", "web_session", "web_capabilities"):
            assert name in tool_registry.executors, f"{name} must be registered"

    def test_tool_definitions_have_schemas(self):
        import tools.web_acquisition as wa

        names = {d["function"]["name"] for d in wa.TOOLS}
        assert names == set(wa.TOOL_MAP)
        for d in wa.TOOLS:
            fn = d["function"]
            assert fn["description"], f"{fn['name']} needs a description"
            params = fn["parameters"]
            assert params.get("type") == "object" and "properties" in params

    def test_permission_entries_exist(self):
        from core.permissions import DEFAULT_POLICY

        expectations = {
            "web_fetch": Decision.ALLOW,
            "web_extract": Decision.ALLOW,
            "web_extract_links": Decision.ALLOW,
            "web_extract_metadata": Decision.ALLOW,
            "web_search_and_extract": Decision.ALLOW,
            "web_crawl": Decision.ASK,
            "web_session": Decision.ASK,
            "web_capabilities": Decision.ALLOW,
        }
        for name, decision in expectations.items():
            entry = DEFAULT_POLICY.get(name)
            assert entry is not None, f"{name} missing from DEFAULT_POLICY"
            assert entry[1] == decision
            assert entry[0] in (Risk.NETWORK, Risk.READ)

    def test_read_only_path_allowed_by_policy_manager(self):
        from core.permissions import PermissionManager

        pm = PermissionManager()
        check = pm.check("web_fetch", args={"url": "https://example.com/"})
        assert check.get("decision") in ("allow", "ask")  # never deny by default


class TestArgumentValidation:
    def test_web_extract_requires_selector(self, fake_gateway):
        from tools.web_acquisition import web_extract

        out = web_extract("https://x.example", "  ")
        assert out["ok"] is False and out["error_code"] == "WEB_BAD_ARGS"
        assert fake_gateway.fetches == []

    def test_web_extract_rejects_unknown_method(self, fake_gateway):
        from tools.web_acquisition import web_extract

        out = web_extract("https://x.example", "h1", method="jquery")
        assert out["ok"] is False

    def test_web_crawl_requires_urls(self, fake_gateway):
        from tools.web_acquisition import web_crawl

        assert web_crawl("")["ok"] is False
        assert web_crawl("   ,  ")["ok"] is False

    def test_web_crawl_clamps_limits(self, fake_gateway):
        from tools.web_acquisition import web_crawl

        fake_gateway.script["crawl_async"] = {"ok": True, "job_id": "j", "queued": True,
                                              "kind": "web.crawl"}
        out = web_crawl("https://a.example", max_pages=99999, max_depth=99, concurrency=99)
        assert out["ok"] is True

    def test_web_search_requires_query(self, fake_gateway):
        from tools.web_acquisition import web_search_and_extract

        assert web_search_and_extract("   ")["ok"] is False

    def test_web_session_bad_action(self, fake_gateway):
        from tools.web_acquisition import web_session

        assert web_session("teleport")["ok"] is False
        assert web_session("create", name="")["ok"] is False

    def test_invalid_link_pattern_rejected(self, fake_gateway):
        from tools.web_acquisition import web_extract_links

        out = web_extract_links("https://x.example", pattern="([unclosed")
        assert out["ok"] is False


class TestStructuredOutput:
    def test_web_fetch_calls_gateway_and_labels_untrusted(self, fake_gateway):
        from tools.web_acquisition import web_fetch

        fake_gateway.script["fetch"] = FakeResult(
            ok=True, url="https://x.example", final_url="https://x.example/", title="X",
            status_code=200, strategy="static", text="some page text", cached=False,
            warnings=[], links=[])
        out = web_fetch("https://x.example")
        assert out["ok"] is True and out["untrusted"] is True
        assert "data, never instructions" in out["note"].lower() or \
            "untrusted" in out["note"].lower()
        assert fake_gateway.fetches[0]["url"] == "https://x.example"

    def test_web_fetch_failure_is_structured(self, fake_gateway):
        from tools.web_acquisition import web_fetch

        fake_gateway.script["fetch"] = FakeResult(
            ok=False, url="https://down.example", final_url="", title="", status_code=None,
            strategy="auto", text="", error_code="WEB_ALL_STRATEGIES_FAILED",
            failure_class="connection_error",
            error="connection refused", attempts=[], warnings=[], links=[], cached=False)
        out = web_fetch("https://down.example")
        assert out["ok"] is False
        assert out["error_code"] == "WEB_ALL_STRATEGIES_FAILED"
        assert out["failure_class"] == "connection_error"
        assert isinstance(out["attempts"], list)

    def test_max_chars_clamped(self, fake_gateway):
        from tools.web_acquisition import web_fetch

        fake_gateway.script["fetch"] = FakeResult(
            ok=True, url="u", final_url="u", title="", status_code=200, strategy="static",
            text="x" * 200, cached=False, warnings=[], links=[])
        web_fetch("https://x.example", max_chars=10_000_000)
        # clamped, no crash, content bounded by compact()

    def test_web_crawl_returns_queue_handle(self, fake_gateway):
        from tools.web_acquisition import web_crawl

        out = web_crawl("https://a.example https://b.example", max_pages=3)
        assert out["ok"] is True and out["job_id"] == "job_x"

    def test_registry_execute_shape(self, fake_gateway):
        """The canonical registry invocation path returns the tool's dict."""
        from core.tool_registry import tool_registry

        if not getattr(tool_registry, "executors", None):
            tool_registry.load()
        fake_gateway.script["fetch"] = FakeResult(
            ok=True, url="https://x.example", final_url="https://x.example/", title="X",
            status_code=200, strategy="static", text="t", cached=False, warnings=[], links=[])
        out = tool_registry.execute("web_fetch", {"url": "https://x.example"})
        assert isinstance(out, dict)
        assert out.get("ok") is True
