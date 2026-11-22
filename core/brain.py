"""Which model answers, where that came from, and the one command that fixes it.

A fresh install used to ship an unexamined default (``ollama/llama3.1:8b``) that
nothing installed, so the first answer a person got was the mock echo. This
module resolves a brain that exists on the machine in front of it and always
reports the source of the choice, in words a non-developer can read.

Resolution order is the crew's product bar, mechanically:

1. a model the person named (``HERMUS_MODEL`` or an onboarding choice),
2. a local runtime that is already serving (Ollama, LM Studio, NoLlama),
3. a free-tier key the person pasted (Groq, Mistral, OpenRouter),
4. the offline stub — named as the stub, never dressed up as an answer.

Everything here is detection and wording. It never installs, never pulls, and
never spends a token; the callers decide what to offer.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from typing import Any

OLLAMA_BASE = "http://localhost:11434"
LMSTUDIO_BASE = "http://localhost:1234/v1"
NOLLAMA_BASE = "http://localhost:8010/v1"

# The smallest model that gives a usable first answer on a laptop CPU. 1B keeps
# the download ~0.6 GB and the first token under a second once warm, which is
# what makes the first-run promise ("answer in two seconds") survivable.
SMALLEST_LOCAL_MODEL = "llama3.2:1b"

# Preferred order when several models are already pulled: cheapest usable first.
LOCAL_PREFERENCE = (
    "llama3.2:1b",
    "llama3.2:3b",
    "qwen2.5:1.5b",
    "qwen2.5:3b",
    "phi3:mini",
    "gemma2:2b",
    "llama3.1:8b",
)

# Free tiers, in the order we would rather use them. Every one of these is a
# no-card free plan; the key is pasted by the person and stored in .env.
FREE_TIER = (
    ("groq", "GROQ_API_KEY", "llama-3.1-8b-instant", "https://console.groq.com/keys"),
    ("mistral", "MISTRAL_API_KEY", "open-mistral-nemo", "https://console.mistral.ai/api-keys"),
    ("openrouter", "OPENROUTER_API_KEY", "auto", "https://openrouter.ai/keys"),
)

STUB_MODEL = "mock/mock"

# What the repository ships with before anyone has chosen. These are the values
# ``apply_resolved_default`` is allowed to replace; anything else is a decision
# somebody made, and is left alone.
SHIPPED_DEFAULTS = ("", "ollama/llama3.1:8b", "ollama/llama3.2:1b")


def _http_json(url: str, timeout: float = 1.0) -> Any:
    """GET one JSON document. Returns None on any failure — detection only."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed localhost URLs
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def ollama_serving(base: str | None = None) -> bool:
    return _http_json(f"{(base or OLLAMA_BASE).rstrip('/')}/api/tags", timeout=0.8) is not None


def ollama_models(base: str | None = None) -> list[str]:
    data = _http_json(f"{(base or OLLAMA_BASE).rstrip('/')}/api/tags", timeout=1.5)
    if not isinstance(data, dict):
        return []
    return [str(m.get("name") or "").strip() for m in data.get("models") or [] if m.get("name")]


def ollama_installed() -> bool:
    """Is the Ollama binary on PATH (or in its default Windows install dir)?"""
    if shutil.which("ollama"):
        return True
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        return os.path.isfile(os.path.join(local, "Programs", "Ollama", "ollama.exe"))
    return False


def lmstudio_installed() -> bool:
    """Is LM Studio present (its ``lms`` CLI, or the app itself)?"""
    if shutil.which("lms"):
        return True
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or ""
    candidates = [os.path.join(home, ".lmstudio"), os.path.join(home, ".cache", "lm-studio")]
    if local:
        candidates += [
            os.path.join(local, "Programs", "LM Studio"),
            os.path.join(local, "LM-Studio"),
        ]
    if sys.platform == "darwin":
        candidates.append("/Applications/LM Studio.app")
    return any(os.path.exists(path) for path in candidates)


def _openai_models(base: str) -> list[str]:
    data = _http_json(f"{base.rstrip('/')}/models", timeout=0.45)
    if not isinstance(data, dict):
        return []
    return [str(m.get("id")) for m in data.get("data") or [] if m.get("id")]


_status_cache: tuple[float, dict[str, Any]] | None = None
STATUS_TTL_SECONDS = 4.0


def local_status(force: bool = False) -> dict[str, Any]:
    """Everything the installers and the welcome screen need to decide, one probe.

    Cached for a few seconds and probed concurrently: the first screen must not
    wait on three localhost sockets that nothing is listening on (Windows takes
    about a second per refused connect), so worst case is one timeout, not three.
    """
    global _status_cache
    now = time.monotonic()
    if not force and _status_cache is not None and now - _status_cache[0] < STATUS_TTL_SECONDS:
        return _status_cache[1]

    from concurrent.futures import ThreadPoolExecutor

    def probe_ollama() -> tuple[bool, list[str]]:
        data = _http_json(f"{OLLAMA_BASE.rstrip('/')}/api/tags", timeout=0.8)
        if not isinstance(data, dict):
            return False, []
        models = [str(m.get("name") or "").strip() for m in data.get("models") or [] if m.get("name")]
        return True, models

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="hermus-brain-probe") as pool:
        ollama_job = pool.submit(probe_ollama)
        installed_job = pool.submit(ollama_installed)
        lm_installed_job = pool.submit(lmstudio_installed)
        lm_job = pool.submit(_openai_models, LMSTUDIO_BASE)
        nollama_job = pool.submit(_openai_models, NOLLAMA_BASE)
        ollama_up, models = ollama_job.result()
        installed = installed_job.result()
        lm_installed = lm_installed_job.result()
        lm_models = lm_job.result()
        nollama_models = nollama_job.result()

    status = {
        "ollama": {
            "installed": installed,
            "serving": ollama_up,
            "models": models,
        },
        "lmstudio": {"installed": lm_installed, "serving": bool(lm_models), "models": lm_models},
        "nollama": {"serving": bool(nollama_models), "models": nollama_models},
    }
    _status_cache = (now, status)
    return status


def free_key_status() -> dict[str, bool]:
    return {provider: bool(os.environ.get(env_key)) for provider, env_key, _, _ in FREE_TIER}


def _pick_pulled(models: list[str]) -> str | None:
    """Choose from already-pulled models: a known small one, else the first."""
    if not models:
        return None
    by_lower = {m.lower(): m for m in models}
    for wanted in LOCAL_PREFERENCE:
        if wanted.lower() in by_lower:
            return by_lower[wanted.lower()]
    # ``:latest`` tags are the common case for a hand-pulled model.
    for wanted in LOCAL_PREFERENCE:
        for name in models:
            if name.lower().split(":")[0] == wanted.split(":")[0]:
                return name
    return models[0]


def local_brain(model: str, source: str, provider: str = "ollama") -> dict[str, Any]:
    name = model.split("/", 1)[1] if "/" in model else model
    return {
        "kind": "local",
        "provider": provider,
        "name": name,
        "model": f"{provider}/{model}" if "/" not in model else model,
        "source": source,
        "reachable": True,
        "present": None,
        "reason": "",
    }


def free_brain(provider: str, model: str, env_key: str) -> dict[str, Any]:
    return {
        "kind": "freekey",
        "provider": provider,
        "name": model,
        "model": f"{provider}/{model}",
        "source": f"{provider} free tier — key from your .env ({env_key})",
        "reachable": True,
        "present": None,
        "reason": "",
    }


def stub_brain(reason: str) -> dict[str, Any]:
    return {
        "kind": "stub",
        "provider": "mock",
        "name": "stub",
        "model": STUB_MODEL,
        "source": "offline stub — no model installed",
        "reachable": False,
        "present": None,
        "reason": reason,
    }


def _probe_named(model: str) -> dict[str, Any]:
    """Honour a named model, reporting reachability instead of assuming it."""
    provider = model.split("/", 1)[0].lower() if "/" in model else "ollama"
    name = model.split("/", 1)[1] if "/" in model else model
    reachable = False
    present = None
    reason = ""
    if provider == "ollama":
        if ollama_serving():
            pulled = ollama_models()
            present = name in pulled or any(p.split(":")[0] == name.split(":")[0] for p in pulled)
            reachable = bool(present)
            if not present:
                reason = f"'{name}' is not pulled (run: ollama pull {name})"
        else:
            reason = "Ollama is not running (run: ollama serve)"
    elif provider in ("lmstudio", "nollama"):
        base = LMSTUDIO_BASE if provider == "lmstudio" else NOLLAMA_BASE
        reachable = bool(_openai_models(base))
        if not reachable:
            reason = f"{provider} is not serving on {base}"
    elif provider == "mock":
        reachable, present = True, True
    else:
        env_key = next((k for p, k, _, _ in FREE_TIER if p == provider), None)
        reachable = bool(env_key and os.environ.get(env_key))
        if not reachable:
            reason = f"{provider} needs {env_key or 'an API key'} in your environment"
    return {
        "kind": "stub" if provider == "mock" else ("local" if provider in ("ollama", "lmstudio", "nollama") else "freekey"),
        "provider": provider,
        "name": name,
        "model": model,
        "source": "set by you (HERMUS_MODEL)",
        "reachable": bool(reachable),
        "present": present,
        "reason": reason,
    }


def resolve_brain(preferred: str | None = None, *, status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve once: prefer the named model, then local, then a free key, then the stub."""
    named = (preferred or os.environ.get("HERMUS_MODEL") or "").strip()
    if named:
        return _probe_named(named)

    st = status or local_status()

    if st["ollama"]["serving"]:
        chosen = _pick_pulled(st["ollama"]["models"])
        if chosen:
            return local_brain(chosen, f"Ollama on your machine ({len(st['ollama']['models'])} model(s) pulled)")

    if st["lmstudio"]["serving"]:
        return local_brain(st["lmstudio"]["models"][0], "LM Studio on your machine", provider="lmstudio")

    if st["nollama"]["serving"]:
        return local_brain(st["nollama"]["models"][0], "NoLlama on your GPU/NPU", provider="nollama")

    keys = free_key_status()
    for provider, env_key, model, _ in FREE_TIER:
        if keys.get(provider):
            return free_brain(provider, model, env_key)

    if st["ollama"]["installed"]:
        return stub_brain("Ollama is installed but not running")
    return stub_brain("no local model and no free-tier key")


def pickup_line(brain: dict[str, Any]) -> str:
    """The one sentence first run says out loud, never a silent default."""
    if brain["kind"] == "local":
        return f"answering with {brain['name']} on your machine"
    if brain["kind"] == "freekey":
        return f"answering with {brain['name']} from the {brain['provider']} free tier"
    return "answering with the offline stub — no model installed"


def fix_command(brain: dict[str, Any] | None = None, *, status: dict[str, Any] | None = None) -> str:
    """The one command that turns this brain into a real one."""
    st = status or local_status()
    if brain and brain.get("kind") == "freekey":
        return f"add {brain['provider'].upper()}_API_KEY to .env and restart the gateway"
    if not st["ollama"]["serving"]:
        if st["ollama"]["installed"]:
            return "ollama serve"
        if st.get("lmstudio", {}).get("installed"):
            return "open LM Studio, load a model and start its local server (Developer → Start Server)"
        return "install Ollama from https://ollama.com/download, then run: ollama pull " + SMALLEST_LOCAL_MODEL
    return f"ollama pull {SMALLEST_LOCAL_MODEL}"


def apply_resolved_default(*, status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Point ``config.model`` at something that exists, without overriding a choice.

    Called once at gateway startup. Only acts when the configured model is still
    the untouched shipment default (or empty) and a real brain was found, so a
    person who set ``HERMUS_MODEL`` or picked in onboarding keeps their choice.
    """
    from .config import config

    current = str(getattr(config, "model", "") or "").strip()
    if current and current not in SHIPPED_DEFAULTS:
        return {"applied": False, "model": current, "reason": "a model is already chosen"}

    from . import onboarding

    stored = (onboarding.load().get("brain") or {}).get("model")
    resolved = resolve_brain(preferred=stored, status=status)
    if resolved["kind"] == "stub":
        return {"applied": False, "model": current, "reason": resolved["reason"]}
    config.model = resolved["model"]
    config.ollama_default_model = resolved["name"]
    return {"applied": True, "model": resolved["model"], "source": resolved["source"]}


def warm(model: str, *, timeout: float = 120.0) -> dict[str, Any]:
    """Load the model into memory so the person's first keystroke is not the wait.

    Free and local: an empty-prompt generation, which Ollama answers by loading
    the weights. Never fatal — a cold start only makes the next call slower.
    """
    provider = model.split("/", 1)[0].lower() if "/" in model else "ollama"
    name = model.split("/", 1)[1] if "/" in model else model
    if provider != "ollama":
        return {"warmed": False, "reason": f"warm-up only applies to Ollama (got {provider})"}
    started = time.time()
    body = json.dumps({"model": name, "prompt": "", "keep_alive": "30m"}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed localhost URL
            resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"warmed": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"warmed": True, "seconds": round(time.time() - started, 2)}
