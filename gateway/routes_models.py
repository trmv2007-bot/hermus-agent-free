"""Hardware detection and a ranked vision-model recommendation.

Three endpoints, and the split between them is the whole design:

- ``/models/specs``     -- what this machine is, with a source per value
- ``/models/recommend`` -- the ranked table, computed, explained, no side effects
- ``/models/pull``      -- the only endpoint that downloads anything

Recommendation and installation are separate on purpose. These are multi-
gigabyte downloads, and a product that starts pulling a 3.3 GB model the moment
a settings page is opened has made a decision on the user's behalf that they
did not agree to. This module never downloads; it reports. ``/models/pull`` is
the deliberate click.

The pull is streamed back rather than buffered, so a UI can show real progress
instead of a spinner that means "something is happening somewhere".
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.hardware_spec import detect_specs
from core.model_recommender import load_catalog, recommend

router = APIRouter()


@router.get("/models/specs")
async def get_specs() -> dict[str, Any]:
    """Raw, sourced hardware facts. Never infers a value it cannot measure."""
    return detect_specs().to_dict()


@router.get("/models/recommend")
async def get_recommendation() -> dict[str, Any]:
    """The ranked table. Computed on request -- a busy GPU changes the answer,
    so caching this would make it stale exactly when it matters."""
    try:
        return recommend()
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller
        raise HTTPException(status_code=500, detail=f"recommendation failed: {exc}") from exc


class PullRequest(BaseModel):
    model: str = Field(..., description="Catalog id or an ollama tag, e.g. qwen3-vl:4b")
    confirm: bool = Field(
        default=False,
        description="Must be true. Guards against a UI that fires this on mount.",
    )


def _resolve_tag(model: str) -> str:
    """Accept a catalog id or a raw tag, and refuse anything not in the catalog.

    The catalog is the allowlist. Without this, the pull endpoint is an
    arbitrary-command proxy with a model name in it, and the 'verified' flag on
    each catalog entry -- the thing that stopped us offering moondream2, a name
    that does not exist -- becomes decorative.
    """
    for entry in load_catalog():
        if entry["id"] == model or entry.get("ollama") == model:
            return entry.get("ollama", entry["id"])
    raise HTTPException(
        status_code=400,
        detail=(
            f"{model!r} is not in the verified catalog. Every entry was checked "
            "against the Ollama registry before being listed, and this one was "
            "not -- so it is refused rather than guessed at."
        ),
    )


@router.post("/models/pull")
async def pull_model(req: PullRequest) -> StreamingResponse:
    """Stream a real pull. The only route here that writes gigabytes."""
    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm must be true -- this downloads gigabytes to your disk",
        )
    tag = _resolve_tag(req.model)

    async def events():
        try:
            proc = await asyncio.create_subprocess_exec(
                "ollama", "pull", tag,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            yield f"data: {json.dumps({'type': 'error', 'message': 'ollama is not installed or not on PATH'})}\n\n"
            return

        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                yield f"data: {json.dumps({'type': 'progress', 'line': line, 'model': tag})}\n\n"
        rc = await proc.wait()
        yield f"data: {json.dumps({'type': 'done' if rc == 0 else 'error', 'model': tag, 'returncode': rc})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
