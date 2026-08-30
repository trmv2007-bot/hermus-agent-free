"""The canonical ModelGateway.

Centralizes provider discovery, credential resolution, health/rate state,
capability negotiation, task-fit scoring, primary+fallback selection and outcome
recording (Rebuild spec §11). It delegates to the real modules that already hold
this logic (``provider_resolver``, ``model_capabilities``, ``router2``) — it does
not reimplement them — but exposes ONE public entry point so no caller hand-rolls
a fallback chain.

Corrections the spec requires, honored here:
* Model name keywords are only one score feature, never proof of capability.
* A tool-required mission cannot select a model on name alone; capability is
  probed/known before selection.
* Distinguish "multimodal accepted" from "vision tool integration works".
* Hard reliability requirements override price ranking.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from ..contracts import (ModelRequirement, ModelSelection, ModelGatewayResult,
                         FailureClass, Capability)


class ModelGateway:
    """One facade for provider discovery + model selection + completion."""

    def __init__(self, *, resolver=None, capabilities=None, router=None, fleet=None):
        self._resolver = resolver
        self._capabilities = capabilities
        self._router = router
        self._fleet = fleet
        self._lock = threading.RLock()
        # In-process circuit breaker / rate state keyed by provider.
        self._circuit: dict[str, dict[str, Any]] = {}

    # -- lazy deps -------------------------------------------------------------
    def _resolver_mod(self):
        if self._resolver is not None:
            return self._resolver
        from .. import provider_resolver  # type: ignore
        return provider_resolver

    def _capability_mod(self):
        if self._capabilities is not None:
            return self._capabilities
        from .. import model_capabilities  # type: ignore
        return model_capabilities

    def _router_mod(self):
        if self._router is not None:
            return self._router
        from .. import router2  # type: ignore
        return router2

    # --------------------------------------------------------------------------
    def providers(self, *, probe: bool = False) -> list[dict[str, Any]]:
        """One authoritative provider report (discovery + health + capability)."""
        return self._resolver_mod().list_available_providers(probe=probe)

    def inventory(self) -> list[dict[str, Any]]:
        """Raw credential bundles available to the runtime."""
        return self._resolver_mod().discover_runtime_bundles(include_local=True)

    # --------------------------------------------------------------------------
    def select(self, req: ModelRequirement) -> list[ModelSelection]:
        """Score and return ranked candidate model selections for a requirement.

        Uses the real ``provider_resolver`` to pick the best available bundle and
        ``model_capabilities`` to know, not guess, whether a deployment supports
        the required capabilities. Returns zero or more ranked candidates.
        """
        mod = self._resolver_mod()
        require_tools = req.requires_tools() or req.tools
        # Primary bundle chosen by the resolver's own single fallback decision.
        bundle = mod.select_usable_bundle(
            require_tools=require_tools,
            prefer=req.preferred_providers or None,
        )
        candidates: list[ModelSelection] = []
        if bundle:
            candidates.append(self._bundle_to_selection(bundle, req, reason="resolver.choice"))
        # Add a capability-probed candidate when the resolver picked a model that
        # cannot satisfy a requested capability.
        if req.vision:
            vision_sel = self._find_vision_candidate(req)
            if vision_sel and not any(c.model == vision_sel.model for c in candidates):
                candidates.append(vision_sel)
        # Router2 keyword scoring becomes an additional, lower-weight input.
        router_cand = self._router_candidate(req)
        if router_cand and not any(c.model == router_cand.model for c in candidates):
            candidates.append(router_cand)
        # Sort: capability-satisfied first, then reliability over cost.
        candidates.sort(key=lambda c: (not c.tool_capable if require_tools else 0,
                                       not c.vision_capable if req.vision else 0,
                                       -c.score))
        return candidates

    def choose(self, req: ModelRequirement) -> Optional[ModelSelection]:
        sel = self.select(req)
        return sel[0] if sel else None

    # --------------------------------------------------------------------------
    def complete(self, *, provider: str, model: str, content: Optional[str] = None,
                 tool_calls: Optional[list[dict[str, Any]]] = None,
                 trace_id: Optional[str] = None, **kw) -> ModelGatewayResult:
        """Record a completion outcome and update circuit/rate state.

        This is the observation hook: the actual provider call is delegated to the
        existing LLM/OpenAI-compatible layer; the gateway records the typed outcome
        and updates health/circuit-breaker state so a late failure breaks a flaky
        provider before the next mission.
        """
        started = time.time()
        try:
            # Delegate the call to the canonical completion path if available.
            content, tool_calls, extra = self._invoke_complete(provider, model, trace_id, **kw)
            ok = True
            failure_class = None
            latency = int((time.time() - started) * 1000)
        except Exception as exc:
            content = None
            tool_calls = None
            failure_class = self._classify_failure(exc)
            ok = False
            latency = int((time.time() - started) * 1000)
            extra = {"error_message": str(exc)}
        result = ModelGatewayResult(
            provider=provider, model=model, ok=ok,
            failure_class=failure_class, error_code=extra.get("error_code"),
            error_message=extra.get("error_message"), latency_ms=latency,
            content=content, tool_calls=tool_calls,
            rate_state=extra.get("rate_state"),
            trace_id=trace_id, used_fallback=bool(extra.get("used_fallback")),
            fallback_reason=extra.get("fallback_reason"),
            retryable=self._retryable_failure(failure_class),
        )
        self._record_outcome(provider, result)
        return result

    # -- internals --------------------------------------------------------------
    def _bundle_to_selection(self, bundle: dict[str, Any], req: ModelRequirement,
                             reason: str) -> ModelSelection:
        provider = bundle.get("provider") or "custom"
        model = bundle.get("default_model") or ""
        cap = self._probe_capability(model, provider)
        tool_capable = cap.get("tools") is True
        vision_capable = cap.get("vision") is True
        return ModelSelection(
            provider=provider, model=model,
            score=_score(cap, req), reason=reason,
            capabilities=cap.get("capabilities", []),
            context_window=cap.get("context_window", 0),
            tool_capable=tool_capable, vision_capable=vision_capable,
            base_url=bundle.get("base_url"), api_key_ref=bundle.get("key_ref"),
            free=bool(bundle.get("free")),
        )

    def _probe_capability(self, model: str, provider: str) -> dict[str, Any]:
        cap_mod = self._capability_mod()
        negotiate = getattr(cap_mod, "negotiate", None)
        if negotiate is None:
            return {}
        try:
            report = negotiate(model)
            if report is None:
                return {}
            if isinstance(report, dict):
                return report
            to_dict = getattr(report, "to_dict", None)
            return to_dict() if callable(to_dict) else {}
        except Exception:
            # Unknown capability: do not assume tool/vision support.
            return {}

    def _find_vision_candidate(self, req: ModelRequirement) -> Optional[ModelSelection]:
        cap_mod = self._capability_mod()
        try:
            model = cap_mod.select_compatible_model(None, require_vision=True)
            if model:
                return ModelSelection(provider="ollama", model=str(model),
                                      score=0.5, reason="vision.capability",
                                      vision_capable=True, tool_capable=False)
        except Exception:
            pass
        return None

    def _router_candidate(self, req: ModelRequirement) -> Optional[ModelSelection]:
        try:
            router = self._router_mod()
            # Router exposes a ModelRouter; we only borrow its keyword scoring as
            # one low-weight score feature (not proof of capability).
            if hasattr(router, "ModelRouter"):
                obj = router.ModelRouter()
                if hasattr(obj, "score"):
                    scored = obj.score(req.task)
                    return _router_to_selection(scored)
        except Exception:
            pass
        return None

    def _invoke_complete(self, provider, model, trace_id, **kw) -> tuple:
        """Delegate to the real completion layer.

        If no completion hook is configured we raise so the failure is typed.
        """
        hook = kw.get("_complete")
        if hook is not None:
            return hook(provider, model, trace_id=trace_id)
        from .. import llm  # type: ignore
        if hasattr(llm, "complete"):
            res = llm.complete(provider=provider, model=model, trace_id=trace_id, **kw)
            if isinstance(res, dict):
                return res.get("content"), res.get("tool_calls"), res
            return res, None, {}
        raise RuntimeError("no completion backend configured")

    def _classify_failure(self, exc: Exception) -> str:
        txt = f"{type(exc).__name__} {exc}".lower()
        if "429" in txt or "rate" in txt or "quota" in txt or "limit" in txt:
            return FailureClass.RATE_LIMIT.value
        if "401" in txt or "403" in txt or "auth" in txt or "unauthorized" in txt or "uknown key" in txt:
            return FailureClass.AUTH.value
        if "timeout" in txt or "timed out" in txt:
            return FailureClass.TIMEOUT.value
        if "context" in txt and ("length" in txt or "window" in txt or "overflow" in txt or "too long" in txt):
            return FailureClass.CONTEXT_OVERFLOW.value
        if "tool" in txt and ("unsupported" in txt or "not support" in txt):
            return FailureClass.TOOL_UNSUPPORTED.value
        if "model" in txt and ("not found" in txt or "invalid" in txt or "unknown" in txt):
            return FailureClass.INVALID_MODEL.value
        if "policy" in txt or "denied" in txt or "permission" in txt:
            return FailureClass.POLICY_DENIED.value
        return FailureClass.NETWORK.value if _is_network_error(exc) else FailureClass.UNKNOWN.value

    def _retryable_failure(self, fc: Optional[str]) -> bool:
        if fc is None:
            return False
        return fc in (FailureClass.RATE_LIMIT.value, FailureClass.TIMEOUT.value,
                      FailureClass.NETWORK.value, FailureClass.CONTEXT_OVERFLOW.value)

    def _record_outcome(self, provider: str, result: ModelGatewayResult) -> None:
        with self._lock:
            st = self._circuit.setdefault(provider, {"failures": 0, "opened_at": None})
            if result.ok:
                st["failures"] = 0
            else:
                st["failures"] += 1
                if st["failures"] >= 3 and st["opened_at"] is None:
                    st["opened_at"] = time.time()

    def health(self) -> dict[str, Any]:
        """Expose circuit-breaker / rate state for the dashboard (no fabrication)."""
        with self._lock:
            return {p: dict(v) for p, v in self._circuit.items()}


_gateway: Optional[ModelGateway] = None
_gateway_lock = threading.Lock()


def get_model_gateway() -> ModelGateway:
    global _gateway
    with _gateway_lock:
        if _gateway is None:
            _gateway = ModelGateway()
        return _gateway


# -- helpers -------------------------------------------------------------------
def _score(cap: dict[str, Any], req: ModelRequirement) -> float:
    score = 0.0
    if cap.get("tools") is True and req.requires_tools():
        score += 3
    if cap.get("vision") is True and req.vision:
        score += 2
    if cap.get("reasoning") is True and Capability.REASONING.value in req.capabilities:
        score += 1
    if "chat" in cap.get("capabilities", []):
        score += 1
    return score


def _router_to_selection(scored: Any) -> Optional[ModelSelection]:
    if isinstance(scored, dict):
        if not scored:
            return None
        model = scored.get("model") or scored.get("name")
        if not model:
            return None
        return ModelSelection(
            provider=str(scored.get("provider") or "custom"), model=str(model),
            score=float(scored.get("score", 0.5)), reason="router.keyword",
            capabilities=[str(x) for x in scored.get("capabilities", [])],
        )
    if scored is None:
        return None
    model = getattr(scored, "model", None) or getattr(scored, "name", None)
    if not model:
        return None
    return ModelSelection(provider=str(getattr(scored, "provider", "custom")),
                          model=str(model), score=float(getattr(scored, "score", 0.5)),
                          reason="router.keyword")


def _is_network_error(exc: Exception) -> bool:
    txt = f"{type(exc).__name__} {exc}".lower()
    return any(k in txt for k in ("connection", "refused", "unreachable", "resolve", "ssl", "dns"))
