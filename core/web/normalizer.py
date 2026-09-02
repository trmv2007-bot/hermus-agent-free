"""WebResult construction from raw Scrapling responses (spec §7/§15).

Pipeline position: acquisition → THIS → model. Responsibilities:

* flatten the Scrapling response into a secrets-free :class:`WebResult`
  (cookies / request headers / proxy metadata are dropped here, on purpose),
* extract title, text, links (absolute), metadata, headings,
* compute content hash + size,
* honor the configured response-size / content-size budgets,
* apply prompt-injection hygiene from :mod:`core.web.sanitize` so anything
  handed toward the model is bounded, control-char-free, and labeled untrusted.

Markdown extraction is best-effort: it requires Scrapling's optional ``ai``
extra (markdownify). When absent, text is still produced and a warning notes
the degradation — never a silent fake.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from .models import LinkInfo, StrategyAttempt, WebResult, content_hash
from .sanitize import detect_injection, sanitize_text
from .security import WebSecurityPolicy

# Text budgets: what a WebResult keeps in memory / hands to the model.
_TEXT_BUDGET = 20_000
_MARKDOWN_BUDGET = 20_000
_HTML_BUDGET = 200_000      # only when explicitly requested
_MAX_LINKS = 500
_MAX_METADATA_FIELDS = 24

_SKIP_LINK_PREFIXES = ("javascript:", "mailto:", "tel:", "data:", "#", "about:")


def _response_text(response: Any) -> str:
    try:
        return sanitize_text(response.get_all_text() or "", _TEXT_BUDGET * 2)
    except Exception:
        return ""


def _response_title(response: Any) -> str:
    try:
        matches = response.css("title::text")
        first = matches.get() if matches else None
        return sanitize_text(first or "", 300)
    except Exception:
        return ""


def _response_links(response: Any, base_url: str) -> list[LinkInfo]:
    out: list[LinkInfo] = []
    seen: set[str] = set()
    try:
        hrefs = response.css("a::attr(href)").getall() or []
    except Exception:
        return out
    for href in hrefs[: _MAX_LINKS * 3]:
        href = (href or "").strip()
        if not href or href.startswith(_SKIP_LINK_PREFIXES):
            continue
        absolute = urljoin(base_url or "about:blank", href)
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(LinkInfo(url=absolute, text=""))
        if len(out) >= _MAX_LINKS:
            break
    return out


def _response_metadata(response: Any) -> dict[str, str]:
    meta: dict[str, str] = {}
    try:
        for node in (response.css("meta[name], meta[property]") or [])[:60]:
            try:
                key = node.attrib.get("name") or node.attrib.get("property") or ""
                value = node.attrib.get("content") or ""
                if key and value:
                    meta[key.strip().lower()[:60]] = sanitize_text(value, 300)
            except Exception:
                continue
        for level, tag in ((1, "h1"), (2, "h2")):
            try:
                heads = [sanitize_text(t, 200) for t in
                         response.css(f"{tag}::text").getall()[:8] or []]
                heads = [h for h in heads if h.strip()]
                if heads:
                    meta[f"headings_h{level}"] = " | ".join(heads)
            except Exception:
                continue
    except Exception:
        pass
    return dict(list(meta.items())[:_MAX_METADATA_FIELDS])


def _response_markdown(response: Any, warnings: list[str]) -> str:
    try:
        md = response.markdown(main_content_only=True)
        return sanitize_text(md or "", _MARKDOWN_BUDGET)
    except Exception as exc:
        warnings.append(f"markdown extraction unavailable ({type(exc).__name__}) — "
                        "install 'scrapling[ai]' for markdown output")
        return ""


def build_web_result(
    raw: Any,
    *,
    url: str,
    strategy: str,
    policy: WebSecurityPolicy,
    include_html: bool = False,
    want_markdown: bool = True,
    source: str = "scrapling",
    session_name: str = "",
    cached: bool = False,
) -> WebResult:
    """Build the canonical :class:`WebResult` from a backend RawFetch."""
    response = raw.response
    warnings: list[str] = []
    body_hash = content_hash(raw.response.body if response is not None else b"")

    result = WebResult(
        ok=True,
        url=url,
        final_url=raw.final_url or url,
        title=_response_title(response),
        status_code=raw.status,
        content_type=raw.content_type.split(";")[0].strip().lower(),
        strategy=strategy,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=raw.duration_ms,
        text=_response_text(response),
        links=_response_links(response, raw.final_url or url),
        metadata=_response_metadata(response),
        source=source,
        sha256=body_hash,
        size_bytes=raw.size_bytes,
        session_name=session_name,
        cached=cached,
    )
    if want_markdown:
        result.markdown = _response_markdown(response, warnings)
    if include_html and response is not None:
        try:
            result.html = sanitize_text(response.html_content or "", _HTML_BUDGET)
        except Exception:
            result.html = None
    result.warnings.extend(warnings)

    indicators = detect_injection(result.text)
    if indicators:
        result.warnings.append(
            "page content contains AI-directed instruction patterns — treated as data only"
        )
    if result.text and len(result.text.strip()) < 40:
        result.warnings.append("very little text extracted — page may require JavaScript")
    return result


def error_result(
    url: str,
    *,
    strategy: str,
    error: str,
    error_code: str,
    failure_class: str,
    attempts: Optional[list[StrategyAttempt]] = None,
    status_code: Optional[int] = None,
) -> WebResult:
    """A typed, non-throwing failure result for tool surfaces."""
    return WebResult(
        ok=False,
        url=url,
        strategy=strategy,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        error=error,
        error_code=error_code,
        failure_class=failure_class,
        attempts=list(attempts or []),
        status_code=status_code,
    )


def summarize_for_model(result: WebResult, *, max_chars: int) -> str:
    """Smallest useful representation for the ModelGateway (spec §15)."""
    return result.summary(max_chars=max_chars)


def structured_from_response(raw: Any) -> Optional[Any]:
    """Best-effort structured read of a JSON-looking body (spec §14)."""
    response = raw.response
    if response is None:
        return None
    ctype = (raw.content_type or "").lower()
    if "json" not in ctype:
        return None
    try:
        text = response.body.decode("utf-8", errors="replace")
        return json.loads(text)
    except Exception:
        return None


def same_site(url_a: str, url_b: str) -> bool:
    """Registrable-domain-ish comparison used by the crawler's stay-on-site rule."""
    try:
        host_a = (urlparse(url_a).hostname or "").lower()
        host_b = (urlparse(url_b).hostname or "").lower()
    except ValueError:
        return False
    if host_a.startswith("www."):
        host_a = host_a[4:]
    if host_b.startswith("www."):
        host_b = host_b[4:]
    return host_a == host_b
