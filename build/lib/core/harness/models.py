"""Model discovery + resolution for the harness.

Any model the operator has — hosted via API key/auth (env vars or the multikey
vault) or running locally (ollama / lmstudio / nollama) — appears in the
canonical, secret-free runtime catalog (:meth:`ModelGateway.catalog`), which is
built from ``discover_runtime_bundles(include_local=True)``. The harness speaks
that same language, so ``--model`` accepts a flexible spec:

* ``auto`` / ``default`` / omitted — persisted default-role selection, else the
  best catalog model, else ``mock/mock`` (offline fallback, with a note)
* a **role** name (``default``, ``reasoning``, ``vision``, ``coding``,
  ``background``, ``doctor``, ``voice``) — resolved via ``gateway.resolve_model``
* ``provider/model`` — exact ref, validated against the catalog when possible
  (unknown refs pass through and are attempted directly — local models may not
  be listed until probed)
* a bare ``provider`` name (``groq``, ``ollama``, …) — first catalog model of
  that provider
* ``mock/mock`` — always available, no key, no network

Nothing here ever exposes credentials; the catalog is secret-free by design.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..log import get_logger

logger = get_logger(__name__)

#: Always-available offline model.
MOCK_REF = "mock/mock"

#: Well-known role names (mirrors core.models.model_preferences.ModelPreferences.ROLES).
_DEFAULT_ROLES = ("default", "reasoning", "vision", "coding", "background", "doctor", "voice")

#: Providers that need no credential (local runtimes).
LOCAL_PROVIDERS = {"ollama", "lmstudio", "nollama"}


def _roles() -> tuple[str, ...]:
    try:
        from ..models.model_preferences import ModelPreferences

        return tuple(ModelPreferences.ROLES)
    except Exception:
        return _DEFAULT_ROLES


def _gateway():
    from ..models import get_model_gateway

    return get_model_gateway()


def list_models(*, probe: bool = False) -> dict[str, Any]:
    """Secret-free list of every model the runtime can see right now.

    ``probe=False`` is fully offline (stored catalog); ``probe=True`` additionally
    live-probes providers (network). ``mock/mock`` is always included as the
    offline fallback.
    """
    gw = _gateway()
    try:
        catalog = gw.catalog(probe=probe)
    except Exception as e:
        catalog = {"models": [], "providers": [], "error": f"{type(e).__name__}: {e}"}
    models: list[dict[str, Any]] = []
    for row in catalog.get("models") or []:
        ref = str(row.get("ref") or "")
        if not ref:
            continue
        caps = row.get("capabilities") or {}
        models.append(
            {
                "ref": ref,
                "provider": str(row.get("provider") or ""),
                "provider_name": str(row.get("provider_name") or row.get("provider") or ""),
                "source": str(row.get("source") or "stored"),  # live | stored | local
                "local": str(row.get("provider") or "") in LOCAL_PROVIDERS,
                "available": bool(row.get("available", True)),
                "reachable": row.get("reachable"),
                "context_tokens": row.get("context_tokens"),
                "capabilities": {k: v for k, v in caps.items() if v == "yes"},
            }
        )
    if not any(m["ref"] == MOCK_REF for m in models):
        models.append(
            {
                "ref": MOCK_REF,
                "provider": "mock",
                "provider_name": "Mock (offline)",
                "source": "builtin",
                "local": False,
                "available": True,
                "reachable": True,
                "context_tokens": None,
                "capabilities": {"offline": "yes", "tools": "limited"},
            }
        )
    models.sort(key=lambda m: (m["ref"] == MOCK_REF, m["provider"], m["ref"]))
    return {
        "probe": probe,
        "count": len(models),
        "providers": catalog.get("providers") or [],
        "models": models,
    }


def resolve_model_spec(spec: str | None, *, probe: bool = False) -> dict[str, Any]:
    """Resolve a flexible ``--model`` spec to a concrete ``provider/model`` ref.

    Returns ``{"ok", "spec", "ref", "provider", "model", "source", "validated", "note"}``.
    Never raises — an unresolvable spec returns ``ok=False`` with the available
    refs in ``note`` so the CLI can show what *is* configured.
    """
    gw = _gateway()
    spec = (spec or "").strip()
    catalog = list_models(probe=probe)
    refs = [m["ref"] for m in catalog["models"]]

    def _row(ref: str) -> dict[str, Any] | None:
        return next((m for m in catalog["models"] if m["ref"] == ref), None)

    # 1. mock
    if spec in ("", "mock", MOCK_REF):
        return {
            "ok": True,
            "spec": spec or "mock",
            "ref": MOCK_REF,
            "provider": "mock",
            "model": "mock",
            "source": "builtin",
            "validated": True,
            "note": "offline mock model" if spec else "no spec given; mock is the explicit offline choice",
        }

    # 2. auto / default
    if spec in ("auto", "*"):
        p, m = gw.resolve_model("default")
        if p and m:
            ref = f"{p}/{m}"
            return {
                "ok": True,
                "spec": spec,
                "ref": ref,
                "provider": p,
                "model": m,
                "source": "preference" if _row(ref) else "resolver",
                "validated": _row(ref) is not None,
                "note": "resolved via default role selection / catalog ranking",
            }
        real_refs = [r for r in refs if r != MOCK_REF]
        if real_refs:
            ref = real_refs[0]
            p, _, m = ref.partition("/")
            return {
                "ok": True,
                "spec": spec,
                "ref": ref,
                "provider": p,
                "model": m,
                "source": "catalog",
                "validated": True,
                "note": "no default preference; picked first catalog model",
            }
        return {
            "ok": True,
            "spec": spec,
            "ref": MOCK_REF,
            "provider": "mock",
            "model": "mock",
            "source": "fallback",
            "validated": True,
            "note": f"no configured models discovered (API keys/auth/local runtimes); falling back to {MOCK_REF}",
        }

    # 3. role name
    if spec in _roles():
        p, m = gw.resolve_model(spec)
        if p and m:
            ref = f"{p}/{m}"
            return {
                "ok": True,
                "spec": spec,
                "ref": ref,
                "provider": p,
                "model": m,
                "source": "preference",
                "validated": _row(ref) is not None,
                "note": f"resolved role '{spec}'",
            }
        return {
            "ok": False,
            "spec": spec,
            "ref": "",
            "provider": "",
            "model": "",
            "source": "resolver",
            "validated": False,
            "note": f"role '{spec}' has no usable model. Available: {', '.join(refs) or 'none'}",
        }

    # 4. exact provider/model
    if "/" in spec:
        row = _row(spec)
        p, _, m = spec.partition("/")
        return {
            "ok": True,
            "spec": spec,
            "ref": spec,
            "provider": p,
            "model": m,
            "source": "catalog" if row else "passthrough",
            "validated": row is not None,
            "note": "in runtime catalog"
            if row
            else f"not in catalog (yet) — will be attempted directly; known: {', '.join(refs)}",
        }

    # 5. bare provider name -> first model of that provider
    matches = [r for r in refs if r.split("/", 1)[0] == spec.lower()]
    if matches:
        ref = matches[0]
        p, _, m = ref.partition("/")
        return {
            "ok": True,
            "spec": spec,
            "ref": ref,
            "provider": p,
            "model": m,
            "source": "catalog",
            "validated": True,
            "note": f"provider '{spec}' -> {ref} ({len(matches)} model(s) known)",
        }
    if spec.lower() in LOCAL_PROVIDERS:
        return {
            "ok": False,
            "spec": spec,
            "ref": "",
            "provider": spec.lower(),
            "model": "",
            "source": "catalog",
            "validated": False,
            "note": f"local provider '{spec}' has no listed models (runtime not reachable or none pulled). "
            f"Use an explicit ref like '{spec}/<model>'. Known: {', '.join(refs) or 'none'}",
        }
    return {
        "ok": False,
        "spec": spec,
        "ref": "",
        "provider": "",
        "model": "",
        "source": "catalog",
        "validated": False,
        "note": f"unknown model/provider '{spec}'. Available: {', '.join(refs) or 'none'}",
    }


def ping_model(ref: str, *, timeout_s: float = 30.0) -> dict[str, Any]:
    """Live one-shot check that a model actually answers. Returns ok/latency/error.

    Goes through the canonical gateway (``gateway.llm``), so API-key, auth and
    local models are all exercised the same way the agent uses them.
    """
    t0 = time.time()
    result: dict[str, Any] = {"ref": ref, "ok": False, "latency_ms": 0, "content": "", "error": "", "usage": {}}
    try:
        gw = _gateway()
        llm = gw.llm(model=ref)
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(llm.chat, [{"role": "user", "content": "Reply with exactly: OK"}])
            resp = fut.result(timeout=timeout_s)
        finally:
            pool.shutdown(wait=True)
        result["ok"] = True
        result["content"] = (getattr(resp, "content", "") or "")[:200]
        result["usage"] = dict(getattr(resp, "usage", {}) or {})
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    result["latency_ms"] = int((time.time() - t0) * 1000)
    return result
