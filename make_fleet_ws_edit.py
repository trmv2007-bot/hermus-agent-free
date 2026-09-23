"""Insert fleet WS helpers and replace fleet_ws + fleet_screen_ws."""
import pathlib

p = pathlib.Path("gateway/routes_fleet.py")
txt = p.read_text(encoding="utf-8")

helpers = '''
# Event kinds the dashboard agent feed forwards to connected WS clients.
_FLEET_FEED_KINDS = frozenset({
    "state_changed", "broadcast", "result",
    "mission_opened", "mission_terminated",
    "agent.spawned", "agent.updated",
})

def _fleet_ws_auth(websocket):
    import hmac, os
    from core.config import config
    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    if not expected:
        return True
    provided = websocket.query_params.get("token") or websocket.headers.get("X-Hermus-Token")
    return hmac.compare_digest(str(provided or ""), str(expected))

def _fleet_ws_snapshot(reg):
    return {"agents": [_agent_card(a) for a in reg.list()], "count": len(reg.list())}
'''

marker = '@router.websocket("/ws/fleet")'
assert marker in txt
txt = txt.replace(marker, helpers + marker, 1)
print("Step 1 OK")
p.write_text(txt, encoding="utf-8")
