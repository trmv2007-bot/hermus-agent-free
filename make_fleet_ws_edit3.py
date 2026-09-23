"""Add _fleet_event_to_client_msg and _fleet_ws_poll_and_send helpers."""
import pathlib

p = pathlib.Path("gateway/routes_fleet.py")
txt = p.read_text(encoding="utf-8")

poll_helper = '''

def _fleet_event_to_client_msg(reg, ev):
    """Project a FleetBus event into the dashboard client envelope.

    Secrets and full credential material are never included — only public card
    fields (name, state) are projected.
    """
    kind = ev.kind
    content = ev.content
    target = ev.target

    if kind == "state_changed" and isinstance(content, dict):
        agent_id = content.get("agent_id") or target
        agent = reg.get(agent_id) if agent_id else None
        return {
            "type": "fleet.state_changed",
            "seq": ev.seq, "id": ev.id, "ts": ev.ts,
            "agent_id": agent_id,
            "agent_name": agent.name if agent else None,
            "old_state": content.get("old_state"),
            "new_state": content.get("new_state") or (agent.state if agent else None),
            "reason": content.get("reason"),
        }

    if kind == "broadcast":
        return {
            "type": "fleet.broadcast",
            "seq": ev.seq, "id": ev.id, "ts": ev.ts,
            "content": content, "sender": ev.sender,
        }

    if kind == "result" and isinstance(content, dict):
        return {
            "type": "fleet.result",
            "seq": ev.seq, "id": ev.id, "ts": ev.ts,
            "agent_id": target,
            "task_id": content.get("task_id"),
            "executed": content.get("executed"),
            "blocked": content.get("blocked"),
            "needs_approval": content.get("needs_approval"),
        }

    if kind in ("mission_opened", "mission_terminated") and isinstance(content, dict):
        return {
            "type": "fleet." + kind,
            "seq": ev.seq, "id": ev.id, "ts": ev.ts,
            "mission_id": content.get("mission_id"),
            "goal": content.get("goal"),
            "state": content.get("state"),
        }

    if kind in ("agent.spawned", "agent.updated") and target:
        agent = reg.get(target)
        if agent is not None:
            return {
                "type": "fleet.agent_updated",
                "seq": ev.seq, "id": ev.id, "ts": ev.ts,
                "agent_id": target,
                "agent_name": agent.name,
                "state": agent.state,
                "event_kind": kind,
            }
    return None


async def _fleet_ws_poll_and_send(websocket, reg, *, poll_interval_s=1.0):
    """Poll the FleetBus for new events and forward filtered ones to websocket.

    The FleetBus has no push/subscribe primitive, so this closes the gap by
    polling the durable log for events newer than the client's last seen seq
    and forwarding only the dashboard-relevant kinds.

    TODO(Solar-2026-02): replace polling with an asyncio.Event/notify path when
    FleetBus grows a push primitive, so a quiet fleet burns no CPU.
    """
    import asyncio

    bus = reg.bus
    last_seq = 0

    while True:
        try:
            await asyncio.sleep(poll_interval_s)
        except asyncio.CancelledError:
            break

        try:
            new_since = bus.last_seq
            if new_since <= last_seq:
                continue

            new_events = await asyncio.to_thread(bus.read_events_after, last_seq + 1)
            if not new_events:
                last_seq = new_since
                continue

            sent = 0
            for ev in new_events:
                if ev.seq > last_seq:
                    last_seq = ev.seq
                if ev.kind not in _FLEET_FEED_KINDS:
                    continue
                msg = _fleet_event_to_client_msg(reg, ev)
                if msg is None:
                    continue
                try:
                    await websocket.send_json(msg)
                    sent += 1
                except Exception:
                    return
            if sent == 0 and last_seq < new_since:
                last_seq = new_since
        except Exception as exc:
            logger.debug("[routes_fleet] fleet_ws poll error: %s", exc)
            await asyncio.sleep(poll_interval_s)
'''

# Insert before the new fleet_ws decorator
marker = '@router.websocket("/ws/fleet")\nasync def fleet_ws'
assert marker in txt, "fleet_ws marker not found for poll helper insertion"
txt = txt.replace(marker, poll_helper + marker, 1)
print("Step 4 OK")

p.write_text(txt, encoding="utf-8")
