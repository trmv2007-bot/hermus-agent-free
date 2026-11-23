"""Dashboard configuration surface: the safe ``.env`` settings, read and written.

The Control Room could show every subsystem but could not change anything that
lives in ``.env`` — so "configure the agent" meant finding the repository on disk
and editing a file by hand. These two endpoints back the Settings panel in the
Systems console: :mod:`core.config_env` owns the whitelist and the file, this
module is only the HTTP edge over it.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.config_env import CHANGEABLE, SettingError, read_settings, write_setting

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("/env")
def config_env() -> dict:
    """Every setting the dashboard may change, with where its value came from."""
    return read_settings()


@router.get("/env/allowed")
def config_env_allowed() -> dict:
    """The bare whitelist, for a client that wants to render a form offline."""
    return {"count": len(CHANGEABLE), "keys": sorted(CHANGEABLE)}


@router.post("/env")
def config_env_set(payload: dict) -> dict:
    """Persist one setting. Refuses anything outside the whitelist or credential-shaped."""
    try:
        return write_setting(payload.get("key", ""), payload.get("value", ""))
    except SettingError as exc:
        return JSONResponse({"success": False, "error": str(exc), "code": "setting_refused"}, status_code=400)
