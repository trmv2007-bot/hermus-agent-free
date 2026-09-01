"""Semantic Android observation builder (§7).

Transforms a raw UI tree (and screenshot reference) into a compact, model-friendly
observation so the agent reasons in terms of *what is on screen* rather than raw
coordinates: visible text, buttons, fields, labels, bounds, focused/selected/enabled
state, the foreground package, and a screenshot reference. Coordinates remain
available only as the fallback primitive.
"""
from __future__ import annotations

from typing import Any, Optional


def _node_label(node: dict[str, Any]) -> str:
    return (node.get("text") or node.get("desc") or node.get("contentDescription")
            or node.get("id") or "")


def build_observation(ui_tree: dict[str, Any], *, screen: Optional[dict[str, Any]] = None,
                      screenshot_ref: Optional[str] = None) -> dict[str, Any]:
    """Return a semantic observation a model can reason over.

    ``ui_tree`` should be the dict from a transport's ``get_ui_tree`` (with ``nodes``,
    ``package``, ``title``). We group elements by role and expose the actionable ones
    with their label, bounds and state. Screenshot is referenced, not inlined.
    """
    nodes = (ui_tree or {}).get("nodes") or []
    package = (ui_tree or {}).get("package")
    title = (ui_tree or {}).get("title")

    elements = []
    for n in nodes:
        b = n.get("bounds") or [0, 0, 0, 0]
        elements.append({
            "role": _role(n),
            "label": _node_label(n),
            "id": n.get("id"),
            "text": n.get("text"),
            "class": n.get("class"),
            "bounds": {"x": b[0], "y": b[1], "w": b[2] - b[0], "h": b[3] - b[1]},
            "clickable": bool(n.get("clickable")),
            "enabled": bool(n.get("enabled", True)),
            "focused": bool(n.get("focused")),
            "selected": bool(n.get("selected")),
            "checked": bool(n.get("checked")),
        })

    buttons = [e for e in elements if e["role"] == "button"]
    fields = [e for e in elements if e["role"] == "field"]
    # Expose the current value of each input field so the agent can confirm what was
    # typed before acting on it (e.g. tap the commit button).
    fields = [{**f, "value": f.get("text")} for f in fields]
    texts = [e["label"] for e in elements if e["role"] == "text" and e["label"]]

    # Screenshot hash is a cheap change detector the model can use to confirm a
    # state transition happened (see verify.after_changed).
    screen_hash = (screen or {}).get("hash")

    return {
        "package": package,
        "title": title,
        "screenshot": screenshot_ref or screen_hash,
        "visible_text": texts,
        "buttons": buttons,
        "fields": fields,
        "elements": elements,
        "summary": _summary(buttons, fields, texts, package),
    }


def _role(node: dict[str, Any]) -> str:
    cls = (node.get("class") or "").lower()
    if node.get("clickable") and ("button" in cls or node.get("id") in ("add", "ok", "clear")):
        return "button"
    if "edit" in cls or node.get("focused"):
        return "field"
    if "list" in cls and node.get("text"):
        return "text"
    return "text"


def _summary(buttons: list, fields: list, texts: list, package: Optional[str]) -> str:
    btn_labels = [b["label"] for b in buttons if b["label"]]
    parts = []
    if package:
        parts.append(f"app {package}")
    if fields:
        parts.append(f"{len(fields)} input field(s)")
    if btn_labels:
        parts.append(f"buttons: {', '.join(btn_labels)}")
    if texts:
        parts.append(f"text: {' | '.join(texts[:8])}")
    return "; ".join(parts) or f"no semantic content on {package or 'screen'}"
