"""Replace fleet_ws body and fleet_screen_ws."""
import pathlib

p = pathlib.Path("gateway/routes_fleet.py")
txt = p.read_text(encoding="utf-8")

old_ws = '''@router.websocket("/ws/fleet")
async def fleet_ws(websocket: WebSocket):
    """WebSocket live stream: state changes, messages, results, approvals (SPEC §9).

    The client receives a JSON envelope for every fleet bus event that matches
    broadcast kinds or is addressed to the client's session.
    """
    await websocket.accept()
    # Send initial hello message
    await websocket.send_json({"type": "hello", "protocol": "hermus.fleet.v1"})
    # Wait for client to disconnect (like ws_agent does)
    try:
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[routes_fleet] fleet_ws error: %s", exc)
        try:
            await websocket.close()
        except Exception:
            pass'''

new_ws = '''@router.websocket("/ws/fleet")
async def fleet_ws(websocket: WebSocket):
    """WebSocket live stream: state changes, messages, results, approvals (SPEC §9).

    Auth: token from ?token= or X-Hermus-Token; open when no gateway token
    configured. Sends initial snapshot then polls FleetBus for deltas.
    """
    if not _fleet_ws_auth(websocket):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    await websocket.send_json({"type": "hello", "protocol": "hermus.fleet.v1"})

    reg = _get_registry()
    try:
        await websocket.send_json({"type": "snapshot", "data": _fleet_ws_snapshot(reg)})

        import asyncio as _asyncio

        poll_task = _asyncio.create_task(_fleet_ws_poll_and_send(websocket, reg))
        try:
            while True:
                try:
                    await _asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except WebSocketDisconnect:
                    break
                except _asyncio.TimeoutError:
                    continue
                except Exception:
                    break
        finally:
            poll_task.cancel()
            try:
                await poll_task
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[routes_fleet] fleet_ws error: %s", exc)
        try:
            await websocket.close()
        except Exception:
            pass'''

assert old_ws in txt, "old fleet_ws body not found"
txt = txt.replace(old_ws, new_ws, 1)
print("Step 2 OK")

old_screen = '''@router.websocket("/ws/fleet/screen")
async def fleet_screen_ws(websocket: WebSocket):
    """Low-FPS live screen mirror WebSocket (SPEC §9).

    Sends JPEG frames at ~1-2 FPS. Placeholder for now.
    """
    await websocket.accept()
    await websocket.send_json({"type": "hello", "protocol": "hermus.fleet.screen.v1"})
    try:
        while True:
            # Placeholder - real implementation would capture and send screen frames
            await websocket.send_json({
                "type": "frame",
                "data": None,
                "timestamp": None,
                "message": "screen mirror not yet wired in this build",
            })
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[routes_fleet] fleet_screen_ws error: %s", exc)
        try:
            await websocket.close()
        except Exception:
            pass'''

new_screen = '''@router.websocket("/ws/fleet/screen")
async def fleet_screen_ws(websocket: WebSocket):
    """Low-FPS live screen mirror WebSocket (SPEC §9).

    Currently a placeholder; real screen capture is behind /screen/* routes
    (routes_subsystems.py) and the ComputerEventBus. The dashboard screen panel
    connects here once capture is enabled.

    Auth: token from ?token= or X-Hermus-Token; open when no gateway token
    configured (local default).
    """
    if not _fleet_ws_auth(websocket):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    await websocket.send_json({"type": "hello", "protocol": "hermus.fleet.screen.v1"})
    try:
        while True:
            await websocket.send_json({
                "type": "frame",
                "data": None,
                "timestamp": None,
                "message": "screen capture not yet wired in this build",
            })
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[routes_fleet] fleet_screen_ws error: %s", exc)
        try:
            await websocket.close()
        except Exception:
            pass'''

assert old_screen in txt, "old screen WS not found"
txt = txt.replace(old_screen, new_screen, 1)
print("Step 3 OK")

p.write_text(txt, encoding="utf-8")
