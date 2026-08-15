"""Example Hermus plugin: a calendar "safeguard" tool + event echo.

Demonstrates the Phase D plugin API: it registers a tool (callable through the
gateway's ``/plugins/invoke`` endpoint) and subscribes to computer-agent events
so a plugin can react to the live action feed.
"""
from __future__ import annotations

PLUGIN = {
    "name": "Example Safeguard",
    "version": "1.0.0",
    "description": "Example plugin: a demo 'safeguard' tool and an event echo hook.",
    "author": "Hermus",
}


def register(api):
    api.register_tool(
        "example_safeguard_check",
        safeguard_check,
        description="Example plugin safeguard: returns an allow/block verdict.",
        params={
            "action": {"type": "string", "description": "The computer action to evaluate."},
        },
        required=["action"],
    )
    api.subscribe("action_started", lambda event: api.log(f"echo action_started: {event.get('data', {}).get('action')}"))


def safeguard_check(action: str) -> dict:
    """Demo policy: block nothing but annotate risky keywords."""
    blocked = [word for word in ("sudo", "rm -rf", "dd if") if word in (action or "").lower()]
    return {
        "allowed": not blocked,
        "reason": "blocked keywords: " + ", ".join(blocked) if blocked else "ok",
        "annotated": True,
    }
