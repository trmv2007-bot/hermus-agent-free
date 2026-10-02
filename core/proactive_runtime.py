"""Wire proactive automation to HERMUS's canonical EventBus and JobQueue."""
from __future__ import annotations

from typing import Any

from .proactive import ProactiveAutomation


def _enqueue(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    from gateway.queue import job_queue

    task = str(payload.get("task") or "")
    job_payload = {
        "text": task,
        "task": task,
        "platform": "automation",
        "user_id": "automation",
        "automation": payload.get("automation", True),
        "trigger": payload.get("trigger", {}),
    }
    return job_queue.submit(
        kind,
        job_payload,
        session_key="automation",
    ).brief()


automation = ProactiveAutomation(enqueue=_enqueue)


def wire_proactive_automation() -> ProactiveAutomation:
    """Subscribe once to the canonical bus; safe to call repeatedly."""
    from .events import get_bus

    bus = get_bus()
    marker = getattr(bus, "_hermus_proactive_wired", None)
    if marker is not None:
        return automation

    @bus.subscribe()
    def _on_event(event: Any) -> None:
        automation.handle_event(event)

    bus._hermus_proactive_wired = _on_event
    return automation


__all__ = ["automation", "wire_proactive_automation"]
