"""First run: the three choices, the model that answers, and the proof it does.

Everything here is reachable without the control-plane token, exactly like
``/control`` and its assets: a person who has just installed HERMUS has no
token yet, and the first screen they are supposed to see cannot be behind one.

The contract this router implements:

* ``/`` sends a fresh install to ``/welcome`` and a finished one to ``/companion``.
* ``/welcome`` asks three things, each with a default already selected, so
  Enter alone finishes it.
* The last step is a real round trip. A reply that came from the stub, or no
  reply at all, leaves the run incomplete and says why — never a green tick.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from core import brain as brain_mod
from core import onboarding

router = APIRouter(tags=["onboarding"])

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

# Background preparation (start Ollama, pull the smallest model) reports here so
# the welcome screen can show progress without blocking on a 640 MB download.
_prepare_lock = threading.Lock()
_prepare: dict[str, Any] = {"phase": "idle", "detail": "", "done": False, "ok": False, "log": []}


def _html(name: str) -> HTMLResponse:
    path = STATIC / name
    if not path.exists():
        return HTMLResponse(f"{name} is missing from this install", status_code=500)
    return HTMLResponse(path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store, max-age=0"})


def _asset(name: str, media_type: str) -> Response:
    path = STATIC / name
    if not path.exists():
        return Response(f"{name} not found", status_code=404, media_type="text/plain")
    return Response(path.read_text(encoding="utf-8"), media_type=media_type, headers={"Cache-Control": "no-store, max-age=0"})


def redirect_target() -> str:
    """Where ``/`` goes: the companion when set up, the welcome screen otherwise."""
    state = onboarding.load()
    return "/companion" if onboarding.is_complete(state) else "/welcome"


def state_payload() -> dict[str, Any]:
    state = onboarding.load()
    status = brain_mod.local_status()
    stored = (state.get("brain") or {}).get("model")
    resolved = brain_mod.resolve_brain(preferred=stored, status=status)
    return {
        "complete": onboarding.is_complete(state),
        "reason": onboarding.incomplete_reason(state),
        "name": state.get("name") or onboarding.DEFAULT_NAME,
        "permissions": state.get("permissions") or onboarding.DEFAULT_PROFILE,
        "brain": state.get("brain"),
        "resolved": resolved,
        "pickup": brain_mod.pickup_line(resolved),
        "fix": brain_mod.fix_command(resolved, status=status),
        "check": state.get("check"),
        "profiles": {
            key: {"label": value["label"], "sentence": value["sentence"], "detail": value["detail"]}
            for key, value in onboarding.PROFILES.items()
        },
        "defaults": {"name": onboarding.DEFAULT_NAME, "permissions": onboarding.DEFAULT_PROFILE},
        "detection": status,
        "free_keys": brain_mod.free_key_status(),
        "smallest_model": brain_mod.SMALLEST_LOCAL_MODEL,
        "prepare": dict(_prepare),
    }


# -- pages ------------------------------------------------------------------


@router.get("/welcome", response_class=HTMLResponse)
async def welcome_page():
    return _html("welcome.html")


@router.get("/companion", response_class=HTMLResponse)
async def companion_page():
    return _html("companion.html")


@router.get("/static/welcome.css")
async def welcome_css():
    return _asset("welcome.css", "text/css; charset=utf-8")


@router.get("/static/welcome.js")
async def welcome_js():
    return _asset("welcome.js", "application/javascript; charset=utf-8")


@router.get("/static/companion.css")
async def companion_css():
    return _asset("companion.css", "text/css; charset=utf-8")


@router.get("/static/companion.js")
async def companion_js():
    return _asset("companion.js", "application/javascript; charset=utf-8")


# -- state ------------------------------------------------------------------


@router.get("/api/v1/onboarding/state")
async def onboarding_state():
    return state_payload()


@router.post("/api/v1/onboarding/name")
async def set_name(payload: dict[str, Any] | None = None):
    name = str((payload or {}).get("name") or "").strip() or onboarding.DEFAULT_NAME
    name = name[:40]
    state = onboarding.load()
    state["name"] = name
    onboarding.save(state)
    return {"ok": True, "name": name, "complete": onboarding.is_complete(state)}


@router.post("/api/v1/onboarding/permissions")
async def set_permissions(payload: dict[str, Any] | None = None):
    profile = str((payload or {}).get("profile") or onboarding.DEFAULT_PROFILE)
    if profile not in onboarding.PROFILES:
        return JSONResponse({"ok": False, "error": f"unknown profile '{profile}'"}, status_code=400)
    applied = onboarding.apply_profile(profile)
    state = onboarding.load()
    state["permissions"] = profile
    onboarding.save(state)
    return {"ok": True, **applied, "complete": onboarding.is_complete(state)}


@router.post("/api/v1/onboarding/brain")
async def set_brain(payload: dict[str, Any] | None = None):
    """Record the chosen brain. ``choice=local`` may also prepare Ollama for real."""
    payload = payload or {}
    choice = str(payload.get("choice") or "").strip().lower()

    if choice == "stub":
        state = onboarding.load()
        onboarding.record_brain(state, brain_mod.stub_brain("declined every free option"))
        state["declined_all"] = True
        onboarding.save(state)
        return {
            "ok": True,
            "brain": state["brain"],
            "pickup": "answering with the offline stub — no model installed",
            "complete": False,
            "reason": onboarding.incomplete_reason(state),
        }

    if choice == "freekey":
        provider = str(payload.get("provider") or "").strip().lower()
        key = str(payload.get("key") or "").strip()
        known = {p: (env, model) for p, env, model, _ in brain_mod.FREE_TIER}
        if provider not in known:
            return JSONResponse(
                {"ok": False, "error": f"pick one of: {', '.join(known)}", "providers": list(known)}, status_code=400
            )
        if not key:
            return JSONResponse({"ok": False, "error": "paste the key, or choose Watch only and skip"}, status_code=400)
        env_key, model = known[provider]
        stored = _store_key(env_key, key)
        if not stored.get("ok"):
            return JSONResponse({"ok": False, **stored}, status_code=500)
        chosen = brain_mod.free_brain(provider, model, env_key)
        state = onboarding.load()
        onboarding.record_brain(state, chosen)
        state.pop("declined_all", None)
        onboarding.save(state)
        return {
            "ok": True,
            "brain": state["brain"],
            "pickup": brain_mod.pickup_line(chosen),
            "complete": False,
            "reason": onboarding.incomplete_reason(state),
        }

    if choice != "local":
        return JSONResponse({"ok": False, "error": "choice must be local, freekey or stub"}, status_code=400)

    status = brain_mod.local_status()
    if not status["ollama"]["serving"] and not payload.get("allow_install"):
        return {
            "ok": False,
            "needs_install": not status["ollama"]["installed"],
            "installed": status["ollama"]["installed"],
            "error": "Ollama is not answering yet",
            "fix": brain_mod.fix_command(status=status),
            "smallest_model": brain_mod.SMALLEST_LOCAL_MODEL,
        }

    if payload.get("allow_install"):
        _start_prepare()

    fresh = brain_mod.local_status(force=True)
    resolved = brain_mod.resolve_brain(status=fresh)
    state = onboarding.load()
    onboarding.record_brain(state, resolved)
    state.pop("declined_all", None)
    onboarding.save(state)
    _warm_later(resolved["model"])
    return {
        "ok": resolved["kind"] == "local",
        "brain": state["brain"],
        "pickup": brain_mod.pickup_line(resolved),
        "fix": brain_mod.fix_command(resolved, status=fresh),
        "complete": False,
        "reason": onboarding.incomplete_reason(state),
        "prepare": dict(_prepare),
    }


@router.post("/api/v1/onboarding/prepare")
async def prepare_local():
    """Offer, on one click: start Ollama if it is installed, pull the small model."""
    _start_prepare()
    return {"ok": True, "prepare": dict(_prepare)}


@router.get("/api/v1/onboarding/prepare/status")
async def prepare_status():
    return dict(_prepare)


@router.post("/api/v1/onboarding/finish")
async def finish():
    state = onboarding.load()
    if not onboarding.is_complete(state):
        return JSONResponse(
            {"ok": False, "reason": onboarding.incomplete_reason(state), "complete": False},
            status_code=409,
        )
    return {"ok": True, "complete": True, "next": "/companion", "name": state.get("name")}


# -- the real round trip -----------------------------------------------------


FIRST_PROMPT = "What can you do?"
SYSTEM = (
    "You are {name}, a free, local-first desktop companion running on the person's own computer. "
    "Answer in at most three short sentences, plainly, with no preamble."
)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _answer_stream(text: str):
    """A sync generator: Starlette runs it in a thread, so streaming never blocks the loop."""
    state = onboarding.load()
    name = state.get("name") or onboarding.DEFAULT_NAME
    chosen = state.get("brain") or {}
    model = chosen.get("model")
    if not model:
        resolved_now = brain_mod.resolve_brain()
        yield _sse(
            "error",
            {
                "message": "No model is set up yet, so there is nothing to answer with.",
                "fix": brain_mod.fix_command(resolved_now),
            },
        )
        onboarding.save(onboarding.record_check(state, ok=False, model=None, reason="no model chosen"))
        yield _sse("done", {"ok": False, "reason": "no model chosen"})
        return

    resolved = brain_mod.resolve_brain(preferred=model)
    if resolved["kind"] == "stub":
        reason = resolved.get("reason") or "no model is installed"
        fix = brain_mod.fix_command(resolved)
        yield _sse("error", {"message": f"No model can answer yet — {reason}.", "fix": fix, "model": model})
        onboarding.save(onboarding.record_check(state, ok=False, model=model, reason=reason))
        yield _sse("done", {"ok": False, "reason": reason})
        return
    if resolved.get("reachable") is False:
        reason = resolved.get("reason") or "the model is not reachable"
        yield _sse("error", {"message": f"{reason}.", "fix": brain_mod.fix_command(resolved), "model": model})
        onboarding.save(onboarding.record_check(state, ok=False, model=model, reason=reason))
        yield _sse("done", {"ok": False, "reason": reason})
        return

    yield _sse("status", {"state": "loading", "model": resolved["name"], "pickup": brain_mod.pickup_line(resolved)})

    messages = [
        {"role": "system", "content": SYSTEM.format(name=name)},
        {"role": "user", "content": text or FIRST_PROMPT},
    ]
    started = time.time()
    first_at: float | None = None
    parts: list[str] = []
    failure = ""
    try:
        from core.models import get_model_gateway

        llm = get_model_gateway().llm(model=resolved["model"])
        for delta in llm.chat_stream(messages):
            if not delta:
                continue
            if first_at is None:
                first_at = time.time()
                yield _sse("status", {"state": "streaming", "first_token_ms": int((first_at - started) * 1000)})
            parts.append(delta)
            yield _sse("token", {"text": delta})
    except Exception as exc:  # noqa: BLE001 - the person gets the reason, not a traceback
        failure = f"{type(exc).__name__}: {exc}"

    answer = "".join(parts).strip()
    if failure and not answer:
        yield _sse("error", {"message": f"The model did not answer — {failure}", "fix": brain_mod.fix_command(resolved)})
        onboarding.save(onboarding.record_check(state, ok=False, model=resolved["name"], reason=failure))
        yield _sse("done", {"ok": False, "reason": failure})
        return
    if not answer:
        reason = "the model returned nothing"
        yield _sse("error", {"message": f"No answer came back — {reason}.", "fix": brain_mod.fix_command(resolved)})
        onboarding.save(onboarding.record_check(state, ok=False, model=resolved["name"], reason=reason))
        yield _sse("done", {"ok": False, "reason": reason})
        return

    ms = int((first_at - started) * 1000) if first_at else None
    state = onboarding.load()
    onboarding.save(onboarding.record_check(state, ok=True, model=resolved["name"], ms=ms))
    yield _sse(
        "done",
        {
            "ok": True,
            "model": resolved["name"],
            "pickup": brain_mod.pickup_line(resolved),
            "first_token_ms": ms,
            "complete": onboarding.is_complete(onboarding.load()),
        },
    )


@router.post("/api/v1/onboarding/hello")
async def hello(payload: dict[str, Any] | None = None):
    text = str((payload or {}).get("text") or "").strip() or FIRST_PROMPT
    return StreamingResponse(
        _answer_stream(text),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# -- helpers ----------------------------------------------------------------


def _store_key(env_key: str, value: str) -> dict[str, Any]:
    """Write one key into the project ``.env``, preserving every other line."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    try:
        existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    except OSError as exc:
        return {"ok": False, "error": f"could not read .env: {exc}"}
    out: list[str] = []
    replaced = False
    for line in existing:
        if line.strip().startswith(f"{env_key}="):
            out.append(f"{env_key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{env_key}={value}")
    try:
        env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
        try:
            env_path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        return {"ok": False, "error": f"could not write .env: {exc}"}
    os.environ[env_key] = value
    return {"ok": True, "path": str(env_path), "stored": env_key}


def _warm_later(model: str) -> None:
    def run() -> None:
        try:
            brain_mod.warm(model)
        except Exception:  # noqa: BLE001 - warm-up is an optimisation, never a failure
            pass

    threading.Thread(target=run, daemon=True, name="hermus-onboarding-warm").start()


def _log(line: str) -> None:
    with _prepare_lock:
        _prepare["log"].append(line)
        _prepare["log"] = _prepare["log"][-30:]
        _prepare["detail"] = line


def _start_prepare() -> None:
    """Start Ollama if needed and pull the smallest model, in the background."""
    with _prepare_lock:
        if _prepare.get("phase") == "running":
            return
        _prepare.update({"phase": "running", "detail": "checking your machine", "done": False, "ok": False, "log": []})
    threading.Thread(target=_prepare_worker, daemon=True, name="hermus-onboarding-prepare").start()


def _prepare_worker() -> None:
    try:
        status = brain_mod.local_status()
        if not status["ollama"]["serving"]:
            if not status["ollama"]["installed"]:
                _log("Ollama is not installed. Install it from https://ollama.com/download, then press Check again.")
                with _prepare_lock:
                    _prepare.update({"phase": "needs_install", "done": True, "ok": False})
                return
            _log("starting Ollama")
            creationflags = 0x08000000 if sys.platform == "win32" else 0
            subprocess.Popen(  # noqa: S603 - fixed argv, the Ollama binary we just detected
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            for _ in range(30):
                time.sleep(1)
                if brain_mod.ollama_serving():
                    break
            else:
                _log("Ollama did not start within 30 seconds. Run 'ollama serve' yourself and press Check again.")
                with _prepare_lock:
                    _prepare.update({"phase": "failed", "done": True, "ok": False})
                return
            _log("Ollama is up")

        if brain_mod.ollama_models():
            resolved = brain_mod.resolve_brain()
            _warm_later(resolved["model"])
            _log(f"{resolved['name']} is already pulled")
            state = onboarding.load()
            onboarding.record_brain(state, resolved)
            onboarding.save(state)
            with _prepare_lock:
                _prepare.update({"phase": "done", "done": True, "ok": True})
            return

        model = brain_mod.SMALLEST_LOCAL_MODEL
        _log(f"pulling {model} (about 0.6 GB, once)")
        proc = subprocess.Popen(  # noqa: S603 - fixed argv
            ["ollama", "pull", model],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line:
                _log(line[-160:])
        proc.wait()
        if proc.returncode != 0:
            _log(f"'ollama pull {model}' failed. Pull it yourself, then press Check again.")
            with _prepare_lock:
                _prepare.update({"phase": "failed", "done": True, "ok": False})
            return
        resolved = brain_mod.resolve_brain()
        _warm_later(resolved["model"])
        state = onboarding.load()
        onboarding.record_brain(state, resolved)
        onboarding.save(state)
        _log(f"{model} is ready")
        with _prepare_lock:
            _prepare.update({"phase": "done", "done": True, "ok": True})
    except Exception as exc:  # noqa: BLE001 - reported to the person, never raised
        _log(f"{type(exc).__name__}: {exc}")
        with _prepare_lock:
            _prepare.update({"phase": "failed", "done": True, "ok": False})


__all__ = ["router", "redirect_target", "state_payload"]
