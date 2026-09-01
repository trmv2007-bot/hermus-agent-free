"""Optional local talking-avatar connector.

This module integrates the useful HeyGem-style capability into Hermus without
pulling in HeyGem's application architecture: a single canonical service wraps
its documented local HTTP pipeline (voice preparation -> cloned audio ->
lip-synced avatar video) so the rest of Hermus uses one owner.

Design constraints honored here:
* optional/local only — missing services degrade into explicit unavailable/error
  results instead of breaking chat or the gateway;
* one owner — gateway routes and tools delegate here rather than hand-rolling
  HTTP calls to the avatar stack;
* no source-copy dependency on HeyGem.ai — this is a fresh connector targeting
  the documented endpoints only.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from .config import config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value: str, default: str = "artifact") -> str:
    text = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or ""))
    text = text.strip("._")
    return text[:120] or default


class AvatarService:
    """One optional owner for local avatar/video generation."""

    def __init__(
        self,
        *,
        tts_base_url: Optional[str] = None,
        face2face_base_url: Optional[str] = None,
        root: Optional[Path] = None,
        timeout_s: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.tts_base_url = (tts_base_url or getattr(config, "heygem_tts_url", "") or "").rstrip("/")
        self.face2face_base_url = (
            face2face_base_url or getattr(config, "heygem_face2face_url", "") or ""
        ).rstrip("/")
        self.root = Path(root) if root else config.resolve_path(getattr(config, "avatar_output_dir", "data/avatar"))
        self.timeout_s = float(timeout_s or getattr(config, "heygem_timeout_s", 120.0) or 120.0)
        self._session = session or requests.Session()
        self.root.mkdir(parents=True, exist_ok=True)
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def voices_dir(self) -> Path:
        return self.root / "voices"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def jobs_dir(self) -> Path:
        return self.root / "jobs"

    # ------------------------------------------------------------------ status
    def status(self, *, probe: bool = False) -> dict[str, Any]:
        services = {
            "tts": {
                "configured": bool(self.tts_base_url),
                "url": self.tts_base_url,
                "reachable": None,
                "detail": None,
            },
            "face2face": {
                "configured": bool(self.face2face_base_url),
                "url": self.face2face_base_url,
                "reachable": None,
                "detail": None,
            },
        }
        if probe:
            for key, url in (("tts", self.tts_base_url), ("face2face", self.face2face_base_url)):
                services[key].update(self._probe(url))

        prompts = sorted(self.voices_dir.glob("*.json"))
        jobs = sorted(self.jobs_dir.glob("*.json"))
        configured = all(row["configured"] for row in services.values())
        probed = any(row.get("reachable") is not None for row in services.values())
        available = configured and all(row.get("reachable") is True for row in services.values())
        return {
            "available": available,
            "configured": configured,
            "probed": probed,
            "backend": "heygem-compatible",
            "local": True,
            "root": str(self.root),
            "services": services,
            "voice_profiles": {"count": len(prompts), "directory": str(self.voices_dir)},
            "jobs": {"count": len(jobs), "directory": str(self.jobs_dir)},
            "pipelines": {
                "voice_prepare": True,
                "audio_clone": True,
                "video_submit": True,
                "video_status": True,
            },
            "note": (
                "Connector targets local HeyGem-style services only. End-to-end render availability depends "
                "on those services being installed and reachable."
            ),
        }

    def _probe(self, url: str) -> dict[str, Any]:
        if not url:
            return {"reachable": False, "detail": "not configured"}
        try:
            response = self._session.get(url, timeout=min(3.0, self.timeout_s))
            return {"reachable": True, "detail": f"http {response.status_code}"}
        except Exception as exc:  # noqa: BLE001 - capability report must not raise
            return {"reachable": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}

    # -------------------------------------------------------------- voice prep
    def prepare_voice(self, reference_audio: str, *, lang: str = "en", fmt: str = "") -> dict[str, Any]:
        audio_path = self._resolve_local_file(reference_audio)
        if audio_path is None:
            return {"success": False, "backend": "heygem-compatible", "error": f"reference audio not found: {reference_audio}"}
        payload = {
            "format": (fmt or audio_path.suffix.lstrip(".") or "wav").lstrip("."),
            "reference_audio": str(audio_path),
            "lang": lang or "en",
        }
        try:
            response = self._session.post(
                f"{self.tts_base_url}/v1/preprocess_and_tran",
                json=payload,
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "backend": "heygem-compatible", "error": f"voice prepare failed: {exc}"}

        code = data.get("code", 0)
        if code not in (0, "0", None):
            return {
                "success": False,
                "backend": "heygem-compatible",
                "error": data.get("msg") or data.get("error") or f"voice prepare refused with code {code}",
                "response": data,
            }

        profile_id = uuid.uuid4().hex[:12]
        profile = {
            "voice_profile_id": profile_id,
            "backend": "heygem-compatible",
            "reference_audio": str(audio_path),
            "lang": lang or "en",
            "asr_format_audio_url": data.get("asr_format_audio_url") or str(audio_path),
            "reference_audio_text": data.get("reference_audio_text") or "",
            "created_at": _now_iso(),
        }
        meta_path = self.voices_dir / f"{profile_id}.json"
        meta_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        return {"success": True, "backend": "heygem-compatible", "voice_profile": profile, "path": str(meta_path)}

    def get_voice_profile(self, voice_profile_id: str) -> Optional[dict[str, Any]]:
        path = self.voices_dir / f"{_safe_name(voice_profile_id, 'voice')}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    # -------------------------------------------------------------- audio synth
    def synthesize_audio(
        self,
        text: str,
        *,
        voice_profile_id: str = "",
        reference_audio: str = "",
        reference_text: str = "",
        lang: str = "en",
        speaker: str = "",
        output_name: str = "",
    ) -> dict[str, Any]:
        spoken = str(text or "").strip()
        if not spoken:
            return {"success": False, "backend": "heygem-compatible", "error": "text required"}

        profile = None
        if voice_profile_id:
            profile = self.get_voice_profile(voice_profile_id)
            if profile is None:
                return {
                    "success": False,
                    "backend": "heygem-compatible",
                    "error": f"voice profile not found: {voice_profile_id}",
                }
        elif reference_audio:
            prepared = self.prepare_voice(reference_audio, lang=lang)
            if not prepared.get("success"):
                return prepared
            profile = prepared.get("voice_profile") or {}
            voice_profile_id = str(profile.get("voice_profile_id") or "")
        else:
            return {
                "success": False,
                "backend": "heygem-compatible",
                "error": "voice_profile_id or reference_audio is required",
            }

        payload = {
            "speaker": speaker or uuid.uuid4().hex,
            "text": spoken,
            "format": "wav",
            "topP": 0.7,
            "max_new_tokens": 1024,
            "chunk_length": 100,
            "repetition_penalty": 1.2,
            "temperature": 0.7,
            "need_asr": False,
            "streaming": False,
            "is_fixed_seed": 0,
            "is_norm": 1,
            "reference_audio": profile.get("asr_format_audio_url") or reference_audio,
            "reference_text": profile.get("reference_audio_text") or reference_text or "",
        }
        try:
            response = self._session.post(
                f"{self.tts_base_url}/v1/invoke",
                json=payload,
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            audio_bytes = response.content
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "backend": "heygem-compatible", "error": f"audio synth failed: {exc}"}

        if not audio_bytes:
            return {"success": False, "backend": "heygem-compatible", "error": "audio synth returned no bytes"}
        name = _safe_name(output_name or payload["speaker"], payload["speaker"])
        out_path = self.audio_dir / f"{name}.wav"
        out_path.write_bytes(audio_bytes)
        return {
            "success": True,
            "backend": "heygem-compatible",
            "voice_profile_id": voice_profile_id or profile.get("voice_profile_id") or "",
            "speaker": payload["speaker"],
            "path": str(out_path),
            "bytes": len(audio_bytes),
            "reference_text": payload["reference_text"],
        }

    # -------------------------------------------------------------- video jobs
    def submit_video(self, audio_path: str, video_path: str, *, code: str = "") -> dict[str, Any]:
        audio = self._resolve_local_file(audio_path)
        video = self._resolve_local_file(video_path)
        if audio is None:
            return {"success": False, "backend": "heygem-compatible", "error": f"audio path not found: {audio_path}"}
        if video is None:
            return {"success": False, "backend": "heygem-compatible", "error": f"video path not found: {video_path}"}
        task_code = _safe_name(code or uuid.uuid4().hex, uuid.uuid4().hex)
        payload = {
            "audio_url": str(audio),
            "video_url": str(video),
            "code": task_code,
            "chaofen": 0,
            "watermark_switch": 0,
            "pn": 1,
        }
        try:
            response = self._session.post(
                f"{self.face2face_base_url}/submit",
                json=payload,
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "backend": "heygem-compatible", "error": f"video submit failed: {exc}"}

        ok = data.get("code") in (10000, "10000", 0, "0", None)
        record = {
            "code": task_code,
            "audio_path": str(audio),
            "video_path": str(video),
            "submitted_at": _now_iso(),
            "response": data,
        }
        (self.jobs_dir / f"{task_code}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        if not ok:
            return {
                "success": False,
                "backend": "heygem-compatible",
                "code": task_code,
                "error": data.get("msg") or data.get("error") or f"video submit refused with code {data.get('code')}",
                "response": data,
                "path": str(self.jobs_dir / f"{task_code}.json"),
            }
        return {
            "success": True,
            "backend": "heygem-compatible",
            "code": task_code,
            "response": data,
            "path": str(self.jobs_dir / f"{task_code}.json"),
            "status_url": f"/speech/avatar/jobs/{task_code}",
        }

    def query_video(self, code: str) -> dict[str, Any]:
        task_code = _safe_name(code, "job")
        try:
            response = self._session.get(
                f"{self.face2face_base_url}/query",
                params={"code": task_code},
                timeout=min(self.timeout_s, 30.0),
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "backend": "heygem-compatible", "error": f"video status failed: {exc}", "code": task_code}

        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        terminal = payload.get("status") in (2, 3)
        success = data.get("code") in (10000, "10000", 0, "0", None) and payload.get("status") != 3
        job_path = self.jobs_dir / f"{task_code}.json"
        if job_path.exists():
            try:
                record = json.loads(job_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                record = {"code": task_code}
        else:
            record = {"code": task_code}
        record["last_status_at"] = _now_iso()
        record["status_response"] = data
        job_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return {
            "success": success,
            "backend": "heygem-compatible",
            "code": task_code,
            "terminal": terminal,
            "state": payload.get("status"),
            "progress": payload.get("progress"),
            "message": payload.get("msg") or data.get("msg"),
            "result": payload.get("result"),
            "response": data,
            "path": str(job_path),
        }

    def render_from_text(
        self,
        text: str,
        avatar_video_path: str,
        *,
        voice_profile_id: str = "",
        reference_audio: str = "",
        reference_text: str = "",
        lang: str = "en",
        code: str = "",
    ) -> dict[str, Any]:
        audio = self.synthesize_audio(
            text,
            voice_profile_id=voice_profile_id,
            reference_audio=reference_audio,
            reference_text=reference_text,
            lang=lang,
            output_name=code or uuid.uuid4().hex[:12],
        )
        if not audio.get("success"):
            return {"success": False, "backend": "heygem-compatible", "audio": audio, "error": audio.get("error")}
        submission = self.submit_video(audio.get("path") or "", avatar_video_path, code=code)
        return {
            "success": bool(submission.get("success")),
            "backend": "heygem-compatible",
            "audio": audio,
            "submission": submission,
            "code": submission.get("code") or code,
            "error": submission.get("error"),
        }

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _resolve_local_file(path_value: str) -> Optional[Path]:
        text = str(path_value or "").strip()
        if not text:
            return None
        path = Path(os.path.expanduser(text))
        if not path.is_absolute():
            path = config.resolve_path(text)
        try:
            path = path.resolve()
        except Exception:  # noqa: BLE001
            pass
        return path if path.exists() and path.is_file() else None


_service: Optional[AvatarService] = None


def get_avatar_service() -> AvatarService:
    global _service
    if _service is None:
        _service = AvatarService()
    return _service


avatar_service = get_avatar_service()
