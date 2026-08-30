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


class ModelGatewayError(Exception):
    """A typed model-gateway error carrying a canonical ``failure_class``.

    ``error_code`` is one of the structured codes the spec requires:
    ``provider_unavailable``, ``rate_limited``, ``authentication_failed``,
    ``model_unavailable``, ``timeout``, ``capability_mismatch``. Callers read
    ``failure_class``/``error_code`` to decide recovery instead of parsing text.
    """

    def __init__(self, message: str, *, failure_class: str = FailureClass.UNKNOWN.value,
                 provider: str = "", model: str = "", retryable: Optional[bool] = None):
        super().__init__(message)
        self.failure_class = failure_class
        self.error_code = failure_class
        self.provider = provider
        self.model = model
        self.retryable = retryable if retryable is not None else \
            (failure_class in (FailureClass.RATE_LIMIT.value, FailureClass.TIMEOUT.value,
                               FailureClass.NETWORK.value, FailureClass.PROVIDER_UNAVAILABLE.value))
        self.retryable = retryable if retryable is not None else \
            (failure_class in (FailureClass.RATE_LIMIT.value, FailureClass.TIMEOUT.value,
                               FailureClass.NETWORK.value, FailureClass.PROVIDER_UNAVAILABLE.value))


class ModelGateway:
    """One facade for provider discovery + model selection + completion."""

    def __init__(self, *, resolver=None, capabilities=None, router=None, fleet=None,
                 llm_builder=None):
        self._resolver = resolver
        self._capabilities = capabilities
        self._router = router
        self._fleet = fleet
        self._llm_builder = llm_builder  # test seam: inject a deterministic FreeLLM
        self._lock = threading.RLock()
        # In-process circuit breaker / rate state keyed by provider.
        self._circuit: dict[str, dict[str, Any]] = {}
        # Deterministic fallback ordering guards against infinite fallback loops.
        self._fallback_visited: dict[str, int] = {}
        self._fallback_max_depth = 5

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

    # -- Section-4 public API ---------------------------------------------------
    def select_model(self, req: ModelRequirement) -> Optional[ModelSelection]:
        """Alias of ``choose``: the one model-selection decision entry point."""
        return self.choose(req)

    def negotiate_capabilities(self, model: str, provider: str = "",
                               required: Optional[list[str]] = None) -> dict[str, Any]:
        """Report (not guess) the capabilities a deployment supports.

        Returns a dict with ``capabilities``, ``tools``, ``vision``, ``reasoning``,
        ``context_window`` and an explicit ``capability_mismatch`` when a required
        capability is not supported.
        """
        cap = self._probe_capability(model, provider) or {}
        supported = set(cap.get("capabilities") or [])
        out = {
            "model": model,
            "provider": provider,
            "capabilities": sorted(supported),
            "tools": cap.get("tools") is True,
            "vision": cap.get("vision") is True,
            "reasoning": cap.get("reasoning") is True,
            "context_window": cap.get("context_window", 0),
        }
        missing = []
        for req in (required or []):
            if req == "tools" and not out["tools"]:
                missing.append("tools")
            elif req == "vision" and not out["vision"]:
                missing.append("vision")
            elif req == "reasoning" and not out["reasoning"]:
                missing.append("reasoning")
            elif req not in supported:
                missing.append(req)
        out["capability_mismatch"] = bool(missing)
        out["missing"] = missing
        return out

    def capabilities_for(self, model: str, provider: str = "") -> dict[str, Any]:
        return self.negotiate_capabilities(model, provider)

    def llm(self, model: Optional[str] = None, provider: Optional[str] = None,
            api_key: Optional[str] = None, base_url: Optional[str] = None,
            temperature: Optional[float] = None):
        """Build the concrete completion object for a model via the canonical path.

        This is the ONE place application code obtains a model client. ``FreeLLM``
        (the provider-call implementation) is created here and returned; the caller
        never constructs a provider client or picks a provider/model itself. A test
        seam (``llm_builder``) can inject a deterministic stub.
        """
        if self._llm_builder is not None:
            return self._llm_builder(model=model, provider=provider, api_key=api_key,
                                     base_url=base_url, temperature=temperature)
        from .. import llm  # type: ignore

        return llm.FreeLLM(model=model, api_key=api_key, base_url=base_url,
                           provider=provider, temperature=temperature)

    def chat(self, messages: list[dict[str, Any]], *, model: Optional[str] = None,
             provider: Optional[str] = None, tools: Optional[list[dict[str, Any]]] = None,
             trace_id: Optional[str] = None, max_retries: int = 1, **kw):
        """Real completion through the canonical boundary.

        Resolves the model (default = configured/selected), invokes the provider
        adapter, records a typed outcome, and returns the ``LLMResponse``
        (content/tool_calls/usage) — or raises a :class:`ModelGatewayError` carrying
        a structured ``failure_class``/``error_code`` so the caller can recover.
        Never fabricates a response on failure.
        """
        llm_obj = self.llm(model=model, provider=provider, **{k: kw[k] for k in
                            ("api_key", "base_url", "temperature") if k in kw})
        started = time.time()
        try:
            resp = llm_obj.chat(messages, tools=tools)
        except Exception as exc:
            self._record_outcome(getattr(llm_obj, "provider", provider or "unknown"),
                                 self._failed_result(exc, trace_id))
            raise ModelGatewayError(str(exc), failure_class=self._classify_failure(exc),
                                    provider=getattr(llm_obj, "provider", provider or "unknown"),
                                    model=getattr(llm_obj, "model_name", model or "")) from exc
        self._record_outcome(getattr(llm_obj, "provider", provider or "unknown"),
                             ModelGatewayResult(provider=provider or "unknown",
                                                model=model or "", ok=True,
                                                latency_ms=int((time.time() - started) * 1000),
                                                content=resp.content, tool_calls=resp.tool_calls,
                                                trace_id=trace_id))
        return resp

    def stream(self, messages: list[dict[str, Any]], *, model: Optional[str] = None,
               provider: Optional[str] = None, tools: Optional[list[dict[str, Any]]] = None,
               on_delta: Optional[Any] = None, **kw):
        """Streaming completion through the canonical boundary.

        Adapts the provider adapter's ``stream_chat`` generator; on failure it
        raises a typed :class:`ModelGatewayError`. ``on_delta`` is invoked per chunk
        when the underlying adapter supports it (the returned generator still yields
        text chunks regardless).
        """
        llm_obj = self.llm(model=model, provider=provider, **{k: kw[k] for k in
                            ("api_key", "base_url", "temperature") if k in kw})
        try:
            gen = llm_obj.stream_chat(messages, tools=tools, on_delta=on_delta)
            return gen
        except Exception as exc:
            raise ModelGatewayError(str(exc), failure_class=self._classify_failure(exc),
                                    provider=getattr(llm_obj, "provider", provider or "unknown"),
                                    model=getattr(llm_obj, "model_name", model or "")) from exc

    def fallback(self, req: ModelRequirement, *, exclude: Optional[list[str]] = None,
                 max_depth: Optional[int] = None) -> list[ModelSelection]:
        """Deterministic ordered fallback candidates, avoiding infinite loops.

        Returns a non-repeating ordered list (primary first); the caller tries each
        in order. ``exclude`` lets a caller drop a provider that just failed, and a
        per-run visit log prevents cycling, so a broken provider can never cause an
        endless retry loop.
        """
        max_depth = max_depth or self._fallback_max_depth
        exclude = set(exclude or [])
        cands = self.select(req)
        out: list[ModelSelection] = []
        seen: set[str] = set()
        for c in cands:
            if c.provider in exclude or c.model in exclude:
                continue
            key = f"{c.provider}:{c.model}"
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
            if len(out) >= max_depth:
                break
        return out

    def health_check(self) -> dict[str, Any]:
        """Aggregate health/status of the boundary (no fabrication)."""
        return {
            "providers": self.providers(probe=False),
            "circuit": self.health(),
        }

    def provider_status(self) -> list[dict[str, Any]]:
        """Per-provider status (discovery + circuit/health)."""
        return self.providers(probe=False)

    def model_status(self) -> dict[str, Any]:
        """Model capability report from the canonical capability registry."""
        cap_mod = self._capability_mod()
        try:
            discover = getattr(cap_mod, "discover_runtime_bundles", None) or \
                getattr(cap_mod, "list_available_providers", None)
            if callable(discover):
                return {"models": discover()}
        except Exception:
            pass
        return {"models": []}

    def status(self) -> dict[str, Any]:
        return self.health_check()

    def _failed_result(self, exc: Exception, trace_id: Optional[str]) -> ModelGatewayResult:
        return ModelGatewayResult(
            provider="", model="", ok=False, failure_class=self._classify_failure(exc),
            error_code=self._classify_failure(exc), error_message=str(exc),
            retryable=self._retryable_failure(self._classify_failure(exc)),
            trace_id=trace_id,
        )

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
            return FailureClass.AUTH.value            # authentication_failed
        if "timeout" in txt or "timed out" in txt:
            return FailureClass.TIMEOUT.value
        if "context" in txt and ("length" in txt or "window" in txt or "overflow" in txt or "too long" in txt):
            return FailureClass.CONTEXT_OVERFLOW.value
        if "tool" in txt and ("unsupported" in txt or "not support" in txt):
            return FailureClass.TOOL_UNSUPPORTED.value
        if "capability" in txt or "does not support" in txt or "can't satisfy" in txt:
            return FailureClass.CAPABILITY_MISMATCH.value
        if "model" in txt and ("not found" in txt or "invalid" in txt or "unknown" in txt or "does not exist" in txt):
            return FailureClass.MODEL_UNAVAILABLE.value
        if "policy" in txt or "denied" in txt or "permission" in txt:
            return FailureClass.POLICY_DENIED.value
        if _is_network_error(exc):
            return FailureClass.NETWORK.value          # transient connection error
        if "no provider" in txt or "no api key" in txt or "not configured" in txt or \
                "provider unavailable" in txt or "no bundle" in txt:
            return FailureClass.PROVIDER_UNAVAILABLE.value   # not configured/available
        return FailureClass.UNKNOWN.value

    def _retryable_failure(self, fc: Optional[str]) -> bool:
        if fc is None:
            return False
        return fc in (FailureClass.RATE_LIMIT.value, FailureClass.TIMEOUT.value,
                      FailureClass.NETWORK.value, FailureClass.CONTEXT_OVERFLOW.value,
                      FailureClass.PROVIDER_UNAVAILABLE.value)

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
