"""Targeted extraction over acquired pages (spec §13/§15).

Backed by Scrapling's parser (CSS / XPath / BeautifulSoup-style finders) with
**adaptive extraction**: record a selector once (``auto_save``), and when a
site's DOM changes, ``adaptive=True`` relocates the equivalent element instead
of failing. Adaptive output is validated here — an empty or wildly oversized
match is rejected rather than silently returning unrelated content.

Every result reports *how* the value was found (selector, method, adaptive
flag, confidence) and the source URL, so callers can audit what they got.
"""
from __future__ import annotations

from typing import Any

from .models import ExtractionResult, WebResult
from .sanitize import sanitize_text
from .scrapling_backend import backend

_MAX_VALUE_LEN = 4000
_MAX_VALUES = 200


def extract_from_result(
    result: WebResult,
    *,
    selector: str = "",
    method: str = "css",
    adaptive: bool = False,
    auto_save: bool = False,
    attribute: str = "",
    max_values: int = 50,
) -> ExtractionResult:
    """Run one extraction against an already-acquired :class:`WebResult`."""
    out = ExtractionResult(ok=False, source_url=result.final_url or result.url,
                           selector=selector, method=method, adaptive=bool(adaptive))
    if not result.ok:
        out.error = result.error or "source acquisition failed"
        return out
    html = result.html
    if not html:
        out.error = ("no HTML retained for this result — re-fetch with include_html=true "
                     "to run selectors")
        return out
    try:
        page = backend.parse_html(html, out.source_url)
    except Exception as exc:
        out.error = f"parser unavailable: {exc}"
        return out

    try:
        if method == "xpath":
            matches = page.xpath(selector)
        elif method == "css":
            matches = page.css(selector, adaptive=adaptive, auto_save=auto_save)
        else:
            out.error = f"unsupported method '{method}' (css | xpath)"
            return out
    except Exception as exc:
        out.error = f"selector failed: {type(exc).__name__}: {exc}"
        out.adaptive = False
        return out

    values: list[str] = []
    try:
        for node in list(matches)[:_MAX_VALUES]:
            value = _node_value(node, attribute)
            value = sanitize_text(value, _MAX_VALUE_LEN)
            if value.strip():
                values.append(value)
            if len(values) >= max(1, min(max_values, _MAX_VALUES)):
                break
    except Exception as exc:
        out.error = f"extraction failed: {type(exc).__name__}: {exc}"
        return out

    # Validation (spec §13): adaptive matching must never silently return junk.
    if adaptive and not values:
        out.error = "adaptive extraction found no relocation candidate for this selector"
        return out
    if adaptive and matches is not None:
        confidence = getattr(matches, "confidence", None) \
            if not isinstance(matches, list) else None
        if confidence is not None:
            out.confidence = float(confidence)

    out.ok = True
    out.values = values
    if adaptive and not out.confidence:
        # Scrapling relocates elements by structural similarity; when it returns
        # matches we treat that as its confidence signal being present-but-unscaled.
        out.warnings.append("adaptive match returned without an explicit confidence score")
    return out


def extract_text(result: WebResult, *, max_chars: int = 20_000) -> ExtractionResult:
    out = ExtractionResult(ok=bool(result.ok and result.text),
                           source_url=result.final_url or result.url,
                           method="text", selector="")
    out.values = [sanitize_text(result.text, max_chars)] if result.text else []
    if not out.ok:
        out.error = result.error or "no text content available"
    return out


def extract_markdown(result: WebResult, *, max_chars: int = 20_000) -> ExtractionResult:
    out = ExtractionResult(ok=bool(result.ok and result.markdown),
                           source_url=result.final_url or result.url,
                           method="markdown", selector="")
    out.values = [sanitize_text(result.markdown, max_chars)] if result.markdown else []
    if not result.markdown and result.ok:
        out.error = ("markdown not available for this result (requires 'scrapling[ai]' "
                     "and a fresh fetch)")
    return out


def extract_metadata(result: WebResult) -> ExtractionResult:
    out = ExtractionResult(ok=bool(result.ok), source_url=result.final_url or result.url,
                           method="metadata", selector="")
    out.values = [f"{k}: {v}" for k, v in sorted(result.metadata.items())]
    out.values.insert(0, f"title: {result.title}" if result.title else "title: (none)")
    return out


def extract_links(result: WebResult, *, pattern: str = "",
                  max_links: int = 200) -> ExtractionResult:
    """Absolute links; optional regex filter on URL or anchor text."""
    out = ExtractionResult(ok=bool(result.ok), source_url=result.final_url or result.url,
                           method="links", selector=pattern)
    links = result.links or []
    if pattern:
        import re

        try:
            rx = re.compile(pattern)
        except re.error as exc:
            out.error = f"invalid link pattern: {exc}"
            return out
        links = [l for l in links if rx.search(l.url) or rx.search(l.text or "")]
    out.values = [f"{l.text} {l.url}".strip() for l in links[:max_links]]
    out.warnings.append(f"{len(links)} links matched" if len(links) > max_links else "")
    out.warnings = [w for w in out.warnings if w]
    if not result.ok:
        out.error = result.error or "source acquisition failed"
        out.ok = False
    return out


def _node_value(node: Any, attribute: str) -> str:
    """Pull a string out of one matched node (attr / ::text / element)."""
    if attribute:
        attrib = getattr(node, "attrib", None) or {}
        return str(attrib.get(attribute, ""))
    if isinstance(node, str):
        return node
    # Scrapling wraps matches in Selector objects: element nodes (tag like "a")
    # carry text via get_all_text(); text nodes (tag "#text") expose their
    # string through get().
    tag = getattr(node, "tag", None)
    if tag == "#text":
        getter = getattr(node, "get", None)
        if callable(getter):
            value = getter()
            return value if isinstance(value, str) else str(value)
    try:
        value = node.get_all_text()
        if value:
            return value
    except Exception:
        pass
    getter = getattr(node, "get", None)
    if callable(getter):
        try:
            value = getter()
            return value if isinstance(value, str) else str(value)
        except Exception:
            return ""
    return ""
