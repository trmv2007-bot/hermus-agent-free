"""Regression tests for the JS-shell / content-sufficiency heuristic.

Audit finding #3: the old heuristic classified any HTML larger than ~4 KB as
"JavaScript-required", so a large static article was wrongly treated as a
dynamic candidate. The rule now is: *large HTML ≠ JavaScript-required*. These
tests pin the four canonical cases from the brief.
"""
from __future__ import annotations

from core.web.router import _is_insufficient, _looks_like_js_shell


class FakeResp:
    def __init__(self, html: str, text: str = "", status: int = 200):
        self._html = html
        self._text = text
        self.status = status

    def get_all_text(self):
        return self._text

    @property
    def html_content(self):
        return self._html


# A long, text-rich article (>100 KB) with normal markup.
_BIG_ARTICLE_TEXT = ("This is a substantial paragraph of a real article. " * 400)
_BIG_ARTICLE_HTML = (
    "<html><head><title>Long Read</title></head><body><article>"
    + "".join(f"<p>{_BIG_ARTICLE_TEXT}</p>" for _ in range(5))
    + "</article></body></html>"
)

# A tiny SPA shell: empty mount node, essentially no visible text.
_SPA_SHELL_HTML = (
    "<html><head><title>App</title></head><body>"
    "<div id='root'></div>"
    "<script src='/static/app.bundle.js'></script>"
    "</body></html>"
)

# A static page that HAPPENS to carry scripts (analytics) but has real content.
_STATIC_WITH_SCRIPTS_HTML = (
    "<html><head><title>Docs</title>"
    "<script>window.dataLayer=[];</script></head><body>"
    "<main><h1>Installation guide</h1>"
    "<p>" + ("Follow these steps to install the package correctly. " * 40) + "</p>"
    "</main>"
    "<script src='https://analytics.example/track.js'></script></body></html>"
)

# A script-dominated shell: almost no text, HTML is mostly a huge inline bundle.
_SCRIPT_DOMINATED_HTML = (
    "<html><head><title>SPA</title></head><body><div id='app'></div>"
    "<script>" + ("var x=1;" * 5000) + "</script></body></html>"
)


class TestCaseA_LargeStaticStaysStatic:
    def test_large_text_rich_html_is_not_a_shell(self):
        resp = FakeResp(_BIG_ARTICLE_HTML, text=_BIG_ARTICLE_TEXT)
        assert len(_BIG_ARTICLE_HTML) > 100_000, "fixture must be genuinely large"
        assert _looks_like_js_shell(resp) is False
        assert _is_insufficient(resp, 200) is False


class TestCaseB_SmallSpaShellEscalates:
    def test_tiny_spa_shell_is_a_dynamic_candidate(self):
        resp = FakeResp(_SPA_SHELL_HTML, text="")
        assert _looks_like_js_shell(resp) is True
        assert _is_insufficient(resp, 200) is True


class TestCaseC_StaticWithScriptsStaysStatic:
    def test_static_page_with_analytics_scripts_is_not_a_shell(self):
        text = "Installation guide " + ("Follow these steps to install the package correctly. " * 40)
        resp = FakeResp(_STATIC_WITH_SCRIPTS_HTML, text=text)
        assert _looks_like_js_shell(resp) is False
        assert _is_insufficient(resp, 200) is False


class TestCaseD_DynamicContentTriggersEscalation:
    def test_script_dominated_shell_with_no_text_escalates(self):
        resp = FakeResp(_SCRIPT_DOMINATED_HTML, text="")
        assert _looks_like_js_shell(resp) is True
        assert _is_insufficient(resp, 200) is True

    def test_hydration_marker_with_little_text_escalates(self):
        html = ("<html><body><div id='container'></div>"
                "<script>window.__NEXT_DATA__={}</script></body></html>")
        resp = FakeResp(html, text="loading")
        assert _looks_like_js_shell(resp) is True


class TestBoundaries:
    def test_meaningful_text_over_ceiling_never_a_shell(self):
        # Even with a mount div present, real extracted text wins.
        html = "<html><body><div id='root'></div></body></html>"
        text = "Real rendered content. " * 20  # > 200 chars
        # NOTE: an explicit empty-root marker is still a shell signal; use a
        # populated root to prove the text-ceiling rule.
        html2 = "<html><body><div id='root'>x</div><p>" + text + "</p></body></html>"
        resp = FakeResp(html2, text=text)
        assert _looks_like_js_shell(resp) is False

    def test_empty_response_is_insufficient(self):
        assert _is_insufficient(None, None) is True

    def test_http_error_is_insufficient(self):
        resp = FakeResp("<html><body>ok</body></html>", text="ok")
        assert _is_insufficient(resp, 500) is True
