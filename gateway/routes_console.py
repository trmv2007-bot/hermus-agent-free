"""Console API — the four routes that serve every panel in :mod:`core.console`.

The control room generates its own surface from ``core.console.manifest()``, so
this module stays deliberately tiny: one route serves the manifest, one serves
live panel data, one serves a single panel projection, and one dispatches the
handful of actions whose owner has no HTTP endpoint of its own. Everything else
the console can do is executed by the browser against the endpoint the manifest
names — the console adds no parallel path to any capability.

Two things are never faked here:

* **Endpoint verification.** Every endpoint a panel claims is checked against
  this gateway's own live route table. A renamed route shows up as
  ``verified: false`` for that panel instead of a dead button.
* **Probe honesty.** A panel whose owner raises reports ``unavailable`` with the
  error text. Nothing is substituted to make the console look complete.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from core import console

router = APIRouter(prefix="/api/v1/console", tags=["console"])


def _route_index() -> set[str]:
    """Flatten this app's live route table into ``"METHOD /path"`` strings.

    FastAPI wraps included routers, so the walk descends into the original
    router when it finds one. Trying/excepting per object keeps this working
    across FastAPI versions rather than pinning one internal attribute name.
    """
    from gateway.gateway import app

    found: set[str] = set()

    def walk(routes: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None and hasattr(original, "routes"):
                walk(original.routes, depth + 1)
                continue
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None) or ()
            if path:
                for method in methods:
                    if method not in ("HEAD", "OPTIONS"):
                        found.add(f"{method.upper()} {path}")
            sub = getattr(route, "routes", None)
            if sub and not path:
                walk(sub, depth + 1)

    walk(app.routes)
    return found


def _verify(endpoints: tuple[str, ...], known: set[str]) -> list[dict[str, Any]]:
    verified = []
    for raw in endpoints:
        method, _, path = raw.partition(" ")
        verified.append({"endpoint": raw, "method": method, "path": path, "verified": raw in known})
    return verified


@router.get("/manifest")
async def console_manifest() -> dict[str, Any]:
    """Every panel Hermus can show, with each declared endpoint's real status."""
    known = _route_index()
    data = console.manifest()
    for panel in data["panels"]:
        panel["endpoint_status"] = _verify(tuple(panel["endpoints"]), known)
        panel["verified"] = all(item["verified"] for item in panel["endpoint_status"])
    data["verified_panels"] = sum(1 for p in data["panels"] if p["verified"])
    data["route_count"] = len(known)
    return data


@router.get("/panels")
async def console_panels(
    ids: str = Query("", description="Comma-separated panel ids; empty probes every panel"),
    workers: int = Query(8, ge=1, le=16, description="Concurrent probe limit"),
) -> dict[str, Any]:
    """Live state of every panel, read from each panel's canonical owner.

    Probes touch SQLite, the filesystem and (for a few) the local engine, so the
    work runs off the event loop with a bounded fan-out.
    """
    selected = [i.strip() for i in ids.split(",") if i.strip()]
    unknown = [i for i in selected if console.get(i) is None]
    if unknown:
        raise HTTPException(status_code=404, detail=f"unknown panel(s): {', '.join(unknown)}")

    started = asyncio.get_running_loop().time()
    limit = asyncio.Semaphore(workers)

    async def one(panel_id: str) -> dict[str, Any]:
        async with limit:
            return await asyncio.to_thread(console.probe, panel_id)

    results = await asyncio.gather(*(one(p.id) for p in (console.panels() if not selected else [console.get(i) for i in selected])))
    ready = sum(1 for r in results if r["status"] == "ready")
    return {
        "count": len(results),
        "ready": ready,
        "unavailable": len(results) - ready,
        "ms": round((asyncio.get_running_loop().time() - started) * 1000, 2),
        "groups": console.groups(),
        "panels": results,
    }


@router.get("/projection/{panel_id}")
async def console_projection(panel_id: str) -> dict[str, Any]:
    """One panel's projection — for owners that have no HTTP endpoint yet."""
    if console.get(panel_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown panel {panel_id!r}")
    return await asyncio.to_thread(console.probe, panel_id)


@router.post("/action/{panel_id}/{action}")
async def console_action(panel_id: str, action: str) -> JSONResponse:
    """Dispatch a console action whose owner has no HTTP endpoint.

    Only names declared in ``core.console.server_actions`` are dispatchable, so
    this route cannot become a generic "call anything" surface.
    """
    if console.get(panel_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown panel {panel_id!r}")
    try:
        handler = console._server_action(panel_id, action)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"panel {panel_id!r} declares no server action {action!r}",
        ) from None
    try:
        result = await asyncio.to_thread(handler)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"success": False, "panel": panel_id, "action": action, "error": f"{type(exc).__name__}: {exc}"},
        )
    return JSONResponse(content={"success": True, "panel": panel_id, "action": action, "result": result})


__all__ = ["router"]
