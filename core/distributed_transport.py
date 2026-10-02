"""Authenticated, expiring job-envelope primitives for distributed HERMUS."""
from __future__ import annotations
import hashlib,hmac,json,time,uuid
from dataclasses import dataclass,asdict
from typing import Any
@dataclass
class JobEnvelope:
    id:str; source_node:str; target_node:str; job_id:str; kind:str; payload:dict[str,Any]; issued_at:float; expires_at:float; nonce:str; signature:str=""
class EnvelopeSigner:
    def __init__(self,secret:str):
        if not secret: raise ValueError("transport secret required")
        self._secret=secret.encode()
    def _canonical(self,e:JobEnvelope)->bytes:
        d=asdict(e); d["signature"]=""; return json.dumps(d,sort_keys=True,separators=(",",":")).encode()
    def sign(self,e:JobEnvelope)->JobEnvelope:
        e.signature=hmac.new(self._secret,self._canonical(e),hashlib.sha256).hexdigest(); return e
    def verify(self,e:JobEnvelope)->bool:
        if e.expires_at<time.time(): return False
        expected=hmac.new(self._secret,self._canonical(e),hashlib.sha256).hexdigest()
        return bool(e.signature) and hmac.compare_digest(expected,e.signature)
def create_envelope(source_node:str,target_node:str,job_id:str,kind:str,payload:dict[str,Any],*,ttl_s:float=300)->JobEnvelope:
    now=time.time(); return JobEnvelope(f"env_{uuid.uuid4().hex[:16]}",source_node,target_node,job_id,kind,dict(payload),now,now+max(1.0,float(ttl_s)),uuid.uuid4().hex)
