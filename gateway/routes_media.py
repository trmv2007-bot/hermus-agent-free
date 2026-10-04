"""Media Lab API for HERMUS image/video creation and gallery management."""
from __future__ import annotations
import asyncio
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from core.media import gallery, generate_image, generate_video, status

router=APIRouter(prefix="/media",tags=["media"])

@router.get("/status")
async def media_status():
    return status()

@router.get("/gallery/{kind}")
async def media_gallery(kind:str,limit:int=40):
    if kind not in {"image","video"}:
        return JSONResponse({"success":False,"error":"kind must be image or video"},status_code=400)
    return {"success":True,"kind":kind,"items":gallery(kind,limit)}

@router.get("/file/{kind}/{name}")
async def media_file(kind:str,name:str):
    if kind not in {"image","video"}:
        return JSONResponse({"success":False,"error":"invalid media kind"},status_code=400)
    root=Path(__import__("core.media",fromlist=["IMAGE_ROOT"]).IMAGE_ROOT if kind=="image" else __import__("core.media",fromlist=["VIDEO_ROOT"]).VIDEO_ROOT)
    target=(root/name).resolve()
    if target.parent!=root.resolve() or not target.is_file():
        return JSONResponse({"success":False,"error":"media file not found"},status_code=404)
    return FileResponse(target)

@router.post("/generate")
async def media_generate(payload:dict|None=None):
    payload=payload or {}; kind=str(payload.get("kind") or "").lower(); prompt=str(payload.get("prompt") or "").strip()
    if kind not in {"image","video"} or not prompt:
        return JSONResponse({"success":False,"error":"kind and prompt are required"},status_code=400)
    result=await asyncio.to_thread(generate_image if kind=="image" else generate_video,prompt,title=str(payload.get("title") or ""))
    code=200 if result.get("success") else (503 if result.get("status")=="offline" else 409)
    return JSONResponse(result,status_code=code)

@router.post("/chat")
async def media_chat(payload:dict|None=None):
    """Creative copilot for the Media Lab. It plans prompts but does not fake generation."""
    payload=payload or {}
    kind=str(payload.get("kind") or "image").lower()
    text=str(payload.get("message") or payload.get("text") or "").strip()
    if kind not in {"image","video"} or not text:
        return JSONResponse({"success":False,"error":"kind and message are required"},status_code=400)
    from core.models import get_model_gateway
    model=str(payload.get("model") or "")
    if not model:
        _p,_m=get_model_gateway().resolve_model("default")
        model=f"{_p}/{_m}" if _p and _m else None
    from core.agent import HermusAgent
    agent=HermusAgent(model=model,session_id=str(payload.get("session_id") or "") or None,mode="chat")
    from core.runtime import _chat_with_compat
    prompt=(f"You are HERMUS Media Copilot for {kind} creation. Help refine the user's "
            f"creative idea, composition, style, motion, camera and negative prompts. "
            f"Do not claim that an image/video was generated. Return concise actionable guidance.\n\nUSER:\n{text}")
    result=await asyncio.to_thread(_chat_with_compat,agent,prompt)
    return {"success":True,"kind":kind,"response":str(result.get("response") or "")}
