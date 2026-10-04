"""HERMUS media workspace: local-first image/video generation with a durable gallery.

Image generation uses a standard ComfyUI HTTP API when available. Video
generation uses a user-supplied ComfyUI workflow template so HERMUS never
pretends that a generic text model is a video generator.
"""
from __future__ import annotations
import json, os, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
MEDIA_ROOT=ROOT/"data"/"media"; IMAGE_ROOT=MEDIA_ROOT/"images"; VIDEO_ROOT=MEDIA_ROOT/"videos"
for _p in (IMAGE_ROOT,VIDEO_ROOT): _p.mkdir(parents=True,exist_ok=True)

def _base_url()->str:
    return str(os.getenv("HERMUS_COMFYUI_URL","http://127.0.0.1:8188")).rstrip("/")

def _request(path:str, *, method="GET", payload:Any=None, timeout=8)->Any:
    body=None; headers={}
    if payload is not None:
        body=json.dumps(payload).encode("utf-8"); headers["Content-Type"]="application/json"
    req=urllib.request.Request(_base_url()+path,data=body,headers=headers,method=method)
    with urllib.request.urlopen(req,timeout=timeout) as resp:
        raw=resp.read()
        return json.loads(raw.decode("utf-8")) if raw else {}

def status()->dict[str,Any]:
    out={"backend":"comfyui","url":_base_url(),"available":False,"image_generation":False,
         "video_generation":False,"video_workflow_configured":bool(os.getenv("HERMUS_COMFYUI_VIDEO_WORKFLOW","").strip())}
    try:
        _request("/system_stats",timeout=3); out["available"]=True
        try:
            info=_request("/object_info/CheckpointLoaderSimple",timeout=4) or {}
            node=info.get("CheckpointLoaderSimple") or {}
            names=((((node.get("input") or {}).get("required") or {}).get("ckpt_name") or [None,[]])[1] or [])
            out["image_generation"]=bool(names); out["image_checkpoints"]=names[:20]
        except Exception as exc: out["image_error"]=str(exc)[:200]
    except Exception as exc: out["error"]=str(exc)[:220]
    return out

def _first_checkpoint()->str|None:
    info=_request("/object_info/CheckpointLoaderSimple",timeout=5) or {}
    node=info.get("CheckpointLoaderSimple") or {}
    values=((((node.get("input") or {}).get("required") or {}).get("ckpt_name") or [None,[]])[1] or [])
    return str(values[0]) if values else None

def _image_workflow(prompt:str,checkpoint:str,seed:int)->dict[str,Any]:
    return {
      "1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":checkpoint}},
      "2":{"class_type":"EmptyLatentImage","inputs":{"width":768,"height":768,"batch_size":1}},
      "3":{"class_type":"CLIPTextEncode","inputs":{"text":prompt,"clip":["1",1]}},
      "4":{"class_type":"CLIPTextEncode","inputs":{"text":"blurry, low quality, distorted, watermark, text","clip":["1",1]}},
      "5":{"class_type":"KSampler","inputs":{"seed":seed,"steps":24,"cfg":7.0,"sampler_name":"euler","scheduler":"normal","denoise":1.0,"model":["1",0],"positive":["3",0],"negative":["4",0],"latent_image":["2",0]}},
      "6":{"class_type":"VAEDecode","inputs":{"samples":["5",0],"vae":["1",2]}},
      "7":{"class_type":"SaveImage","inputs":{"filename_prefix":"hermus_image","images":["6",0]}}
    }

def _wait_for_prompt(prompt_id:str,timeout:int=180)->dict[str,Any]:
    deadline=time.time()+timeout
    while time.time()<deadline:
        try:
            history=_request("/history/"+urllib.parse.quote(prompt_id),timeout=5) or {}
            row=history.get(prompt_id)
            if row:return row
        except Exception: pass
        time.sleep(1)
    raise TimeoutError("media generation timed out")

def _download_view(meta:dict[str,Any],dest:Path)->None:
    qs=urllib.parse.urlencode({"filename":meta.get("filename",""),"subfolder":meta.get("subfolder",""),"type":meta.get("type","output")})
    req=urllib.request.Request(_base_url()+"/view?"+qs,method="GET")
    with urllib.request.urlopen(req,timeout=30) as resp: dest.write_bytes(resp.read())

def generate_image(prompt:str,*,title:str="")->dict[str,Any]:
    prompt=str(prompt or "").strip()
    if not prompt:return {"success":False,"error":"image prompt required"}
    try:
        checkpoint=_first_checkpoint()
        if not checkpoint:return {"success":False,"status":"needs_model","error":"ComfyUI is reachable but has no image checkpoint configured"}
        seed=int.from_bytes(os.urandom(8),"big")%(2**31)
        queued=_request("/prompt",method="POST",payload={"prompt":_image_workflow(prompt,checkpoint,seed)},timeout=12)
        prompt_id=str(queued.get("prompt_id") or "")
        if not prompt_id:return {"success":False,"error":"ComfyUI did not return a prompt id"}
        history=_wait_for_prompt(prompt_id)
        image_meta=None
        for node in (history.get("outputs") or {}).values():
            image_meta=(node.get("images") or [None])[0]
            if image_meta:break
        if not image_meta:return {"success":False,"error":"ComfyUI completed without an image output","prompt_id":prompt_id}
        filename=f"{uuid4().hex[:10]}.png"; dest=IMAGE_ROOT/filename; _download_view(image_meta,dest)
        return {"success":True,"kind":"image","name":title or prompt[:80],"path":str(dest),"url":f"/media/file/image/{filename}","prompt_id":prompt_id}
    except urllib.error.URLError as exc:
        return {"success":False,"status":"offline","error":f"ComfyUI unavailable at {_base_url()} · {exc.reason}"}
    except Exception as exc:return {"success":False,"error":str(exc)[:400]}

def generate_video(prompt:str,*,title:str="")->dict[str,Any]:
    prompt=str(prompt or "").strip(); workflow_path=os.getenv("HERMUS_COMFYUI_VIDEO_WORKFLOW","").strip()
    if not prompt:return {"success":False,"error":"video prompt required"}
    if not workflow_path:return {"success":False,"status":"needs_workflow","error":"Video generation needs HERMUS_COMFYUI_VIDEO_WORKFLOW pointing to a ComfyUI workflow JSON export using the placeholder ${PROMPT}."}
    try:
        workflow=json.loads(Path(workflow_path).read_text(encoding="utf-8"))
        workflow=json.loads(json.dumps(workflow).replace("${PROMPT}",prompt))
        queued=_request("/prompt",method="POST",payload={"prompt":workflow},timeout=12)
        prompt_id=str(queued.get("prompt_id") or "")
        if not prompt_id:return {"success":False,"error":"ComfyUI did not return a prompt id"}
        history=_wait_for_prompt(prompt_id,timeout=600); candidates=[]
        for node in (history.get("outputs") or {}).values():
            candidates.extend(node.get("gifs") or []); candidates.extend(node.get("videos") or []); candidates.extend(node.get("images") or [])
        meta=candidates[0] if candidates else None
        if not meta:return {"success":False,"error":"ComfyUI completed without a video output","prompt_id":prompt_id}
        ext=Path(str(meta.get("filename") or "")).suffix.lower()
        if ext not in {".mp4",".webm",".mov",".gif"}:ext=".mp4"
        filename=f"{uuid4().hex[:10]}{ext}"; dest=VIDEO_ROOT/filename; _download_view(meta,dest)
        return {"success":True,"kind":"video","name":title or prompt[:80],"path":str(dest),"url":f"/media/file/video/{filename}","prompt_id":prompt_id}
    except urllib.error.URLError as exc:return {"success":False,"status":"offline","error":f"ComfyUI unavailable at {_base_url()} · {exc.reason}"}
    except Exception as exc:return {"success":False,"error":str(exc)[:400]}

def gallery(kind:str,limit:int=40)->list[dict[str,Any]]:
    root=IMAGE_ROOT if kind=="image" else VIDEO_ROOT
    ext_ok={".png",".jpg",".jpeg",".webp",".svg"} if kind=="image" else {".mp4",".webm",".mov",".gif"}
    rows=[]
    for p in sorted(root.glob("*"),key=lambda x:x.stat().st_mtime if x.exists() else 0,reverse=True):
        if p.suffix.lower() not in ext_ok:continue
        rows.append({"name":p.name,"url":f"/media/file/{kind}/{p.name}","size":p.stat().st_size,"modified":p.stat().st_mtime})
        if len(rows)>=max(1,min(100,int(limit))):break
    return rows
