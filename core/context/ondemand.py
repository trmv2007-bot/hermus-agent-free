"""Context the model asks for instead of receiving.

Anything that is genuinely useful sometimes and useless usually — architecture
prose, the endpoint inventory, the full tool catalog, deeper memory — lives
behind :func:`read_context`. Keeping it retrievable is what makes it safe to
stop injecting it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Only these documents are readable through the ``docs`` topic. Deep files are
#: developer material; nothing here should reach an arbitrary-path read.
DOC_ALLOWLIST = ("ARCHITECTURE.md", "AUTONOMY_BOUNDARIES.md", "RED_LINES.md", "CAPABILITY_LEDGER.md", "README.md")

MAX_CHARS = 8000

TOPICS = ("architecture", "endpoints", "tools", "memory", "docs")


def read_context(
    topic: str,
    query: str = "",
    *,
    limit: int = 12,
    max_chars: int = MAX_CHARS,
) -> dict[str, Any]:
    """Return one bounded slice of deep context, or the available topics."""
    topic = str(topic or "").strip().lower()
    if topic in ("", "list", "topics"):
        return {"success": True, "topics": list(TOPICS), "hint": "call context_read(topic) to load one"}
    if topic not in TOPICS:
        return {"success": False, "error": f"unknown topic {topic!r}", "topics": list(TOPICS)}

    try:
        reader = _READERS[topic]
        payload = reader(query=query, limit=limit)
    except Exception as exc:
        return {"success": False, "error": f"{topic} read failed: {exc}"}
    payload.setdefault("topic", topic)
    payload["success"] = True
    text = str(payload.get("text") or "")
    cap = max(500, min(int(max_chars or MAX_CHARS), MAX_CHARS))
    if len(text) > cap:
        payload["text"] = text[:cap]
        payload["truncated"] = {"from": len(text), "to": cap}
    return payload


def _read_architecture(query: str, limit: int) -> dict[str, Any]:
    """Section-level excerpt of the developer architecture doc."""
    path = REPO_ROOT / "docs" / "ARCHITECTURE_INVENTORY.md"
    if not path.exists():
        path = REPO_ROOT / "ARCHITECTURE.md"
    if not path.exists():
        return {"text": "", "note": "no architecture document present"}
    sections = _markdown_sections(path.read_text(encoding="utf-8", errors="replace"))
    hits = _match_sections(sections, query, limit)
    body = "\n\n".join(f"## {title}\n{text.strip()}" for title, text in hits)
    return {
        "text": body or f"(no section matched {query!r}; available: {', '.join(list(sections)[:limit])})",
        "sections": list(sections),
    }


def _read_endpoints(query: str, limit: int) -> dict[str, Any]:
    """The control-plane inventory stays here rather than in every prompt."""
    from core.console import _ENDPOINTS

    needle = str(query or "").strip().lower()
    out: list[str] = []
    for group, entries in _ENDPOINTS.items():
        if needle and needle not in group.lower() and not any(needle in e.lower() for e in entries):
            continue
        out.append(f"[{group}] " + "; ".join(entries))
        if len(out) >= limit:
            break
    return {"text": "\n".join(out) or "(no endpoint group matched)", "groups": list(_ENDPOINTS)}


def _read_tools(query: str, limit: int) -> dict[str, Any]:
    """Names first, schemas on request — the catalog is ~68k characters."""
    from core.tool_registry import tool_registry

    defs = tool_registry.get_definitions(allowed={"all"})
    names = sorted(str(d.get("function", {}).get("name") or "") for d in defs)
    needle = str(query or "").strip().lower()
    if needle and needle not in ("schema", "full", "detail"):
        names = [n for n in names if needle in n.lower()]
    with_schema = bool(needle) and len(names) <= 6
    detail: list[dict[str, Any]] = []
    if with_schema:
        for definition in defs:
            fn = definition.get("function") or {}
            if fn.get("name") in names:
                detail.append({"name": fn.get("name"), "description": fn.get("description"), "parameters": fn.get("parameters")})
    return {
        "text": "\n".join(f"- {n}" for n in names[:limit]) or "(no tool names matched)",
        "catalog_size": len(defs),
        "schemas": detail,
    }


def _read_memory(query: str, limit: int) -> dict[str, Any]:
    from core.memory import memory

    ctx = memory.recall_context(str(query or ""), limit=max(1, min(int(limit), 20))) or {}
    kept = ctx.get("kept") or []
    return {
        "text": "\n".join(f"- ({m.get('kind')}, {m.get('score')}) {str(m.get('content') or '')[:300]}" for m in kept),
        "mode": ctx.get("mode"),
        "count": len(kept),
    }


def _read_docs(query: str, limit: int) -> dict[str, Any]:
    needle = str(query or "").strip().lower()
    if needle in ("", "list"):
        return {"text": "Available docs: " + ", ".join(DOC_ALLOWLIST), "docs": list(DOC_ALLOWLIST)}
    name = needle if needle.endswith(".md") else f"{needle}.md"
    if name not in DOC_ALLOWLIST:
        return {"text": "", "error": f"{name} is not readable here", "docs": list(DOC_ALLOWLIST)}
    candidate = (REPO_ROOT / name).resolve()
    if not candidate.is_file() or REPO_ROOT not in candidate.parents:
        return {"text": "", "error": "document missing"}
    sections = _markdown_sections(candidate.read_text(encoding="utf-8", errors="replace"))
    hits = _match_sections(sections, needle, limit) or list(sections.items())[:1]
    return {"text": "\n\n".join(f"## {title}\n{body.strip()}" for title, body in hits), "sections": list(sections)}


def _markdown_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    title = "top"
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("##"):
            sections[title] = "\n".join(buf)
            title = line.lstrip("#").strip() or "untitled"
            buf = []
            continue
        buf.append(line)
    sections[title] = "\n".join(buf)
    return {k: v for k, v in sections.items() if v.strip()}


def _match_sections(sections: dict[str, str], query: str, limit: int) -> list[tuple[str, str]]:
    needle = str(query or "").strip().lower()
    if not needle:
        return list(sections.items())[:limit]
    words = [w for w in needle.replace(",", " ").split() if len(w) > 3]
    scored = []
    for title, body in sections.items():
        haystack = f"{title} {body}".lower()
        score = sum(1 for w in words if w in haystack)
        if score:
            scored.append((score, title, body))
    scored.sort(key=lambda item: -item[0])
    return [(title, body) for _score, title, body in scored[:limit]]


_READERS = {
    "architecture": _read_architecture,
    "endpoints": _read_endpoints,
    "tools": _read_tools,
    "memory": _read_memory,
    "docs": _read_docs,
}
