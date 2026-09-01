"""The canonical Tool Gateway.

Wraps the existing :class:`core.tool_registry.ToolRegistry` (the real tool
autodiscovery/fallback/permission source of truth) and enforces the spec's
one-invocation contract: descriptor resolution, argument validation,
permission/risk gating, a common :class:`ToolResult` envelope, evidence emission,
and typed outcomes the retry/replan policy can consume.

It does **not** reimplement the tools or the registry's own fallback chains — the
registry remains the implementation. The gateway owns the contract, the trace
correlation and the event emission; it never invents a second invocation path.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from ..contracts import ToolDescriptor, ToolResult
from ..events import get_bus
from ..contracts import EventEnvelope, EventType, CommandStatus


class ToolGateway:
    """One facade over the tool registry enforcing the invocation contract."""

    def __init__(self, registry: Any = None, *, bus=None, policy: Any = None):
        if registry is None:
            from ..tool_registry import ToolRegistry, tool_registry as _singleton  # type: ignore
            try:
                # Prefer the real process-wide registry so discovered tools (and
                # their fallback chains) are what the gateway actually drives.
                registry = _singleton
                if not getattr(_singleton, "executors", None):
                    _singleton.load()
            except Exception:
                try:
                    registry = ToolRegistry()
                except Exception:
                    registry = None
        self._registry = registry
        self._bus = bus or get_bus()
        self._policy = policy
        self._lock = threading.RLock()
        self._descriptor_cache: dict[str, ToolDescriptor] = {}

    @property
    def registry(self) -> Any:
        return self._registry

    # -- registry integration -------------------------------------------------
    def _executors(self) -> dict[str, Callable]:
        if self._registry is None:
            return {}
        return getattr(self._registry, "executors", {}) or {}

    def _execute_raw(self, name: str, args: dict[str, Any]) -> tuple[bool, Any, dict]:
        """Invoke one tool through the gateway, classifying typed failures.

        The gateway is the *one* invocation path. It prefers the registry-owned
        ``execute()`` (which also applies permission gates and fallback chains)
        but, when the registry reports an error, the gateway re-classifies the
        error message into a typed, retryable :class:`ToolResult`. If the registry
        has no ``execute`` it invokes the executor directly and catches typed
        exceptions (TimeoutError, PermissionError) so they are never swallowed.
        """
        if self._registry is None:
            return False, None, {"error": "no registry"}
        execute = getattr(self._registry, "execute", None)
        if callable(execute):
            try:
                raw = execute(name, args)
                return _classify_registry_result(raw)
            except Exception as exc:
                return _typed_exception(exc)
        fn = self._executors().get(name)
        if fn is None:
            return False, None, {"error": f"tool '{name}' not found"}
        try:
            out = fn(**args)
            ok = not (isinstance(out, dict) and (out.get("error") or out.get("ok") is False))
            return ok, out, (out if isinstance(out, dict) else {})
        except Exception as exc:
            return _typed_exception(exc)

    # -- public API --------------------------------------------------------------
    def available(self) -> list[str]:
        return list(self._executors().keys())

    def descriptors(self) -> dict[str, ToolDescriptor]:
        result: dict[str, ToolDescriptor] = {}
        for name in self.available():
            result[name] = self.describe(name)
        return result

    def describe(self, name: str, call: Optional[Callable] = None) -> ToolDescriptor:
        desc = self._descriptor_cache.get(name)
        if desc is not None:
            return desc
        if call is None:
            call = self._executors().get(name)
        doc = (getattr(call, "__doc__", "") or "")[:400]
        catalog = self._catalog(name)
        desc = ToolDescriptor(
            name=name,
            description=catalog.get("description") or doc,
            input_schema=catalog.get("parameters") or {},
            source=catalog.get("source") or getattr(call, "__module__", "registry"),
        )
        self._descriptor_cache[name] = desc
        return desc

    def _catalog(self, name: str) -> dict:
        if self._registry is None:
            return {}
        list_tools = getattr(self._registry, "list_tools", None)
        if not callable(list_tools):
            return {}
        try:
            snap = list_tools()
            for c in snap.get("catalog", []):
                if c.get("name") == name:
                    return c
        except Exception:
            pass
        return {}

    def execute(self, name: str, args: Optional[dict[str, Any]] = None, *,
                trace_id: Optional[str] = None, mission_id: Optional[str] = None,
                run_id: Optional[str] = None, actor: str = "agent",
                dry_run: bool = False, timeout_s: Optional[float] = None) -> ToolResult:
        """Execute one tool through the gateway, emitting canonical events.

        Returns a :class:`ToolResult` on **every** path (success, error, blocked,
        timeout, tool-missing) so callers never guess whether an effect happened.
        """
        args = args or {}
        trace_id = trace_id or _new_token()

        # Policy gate (before execution so DENY tools are blocked even if absent)
        blocked = self._gate(name, args)
        if blocked is not None:
            self._emit(name, args, blocked, trace_id, mission_id, run_id, actor)
            return blocked

        started = time.time()
        self._emit_state(name, EventType.COMMAND_STARTED.value, CommandStatus.RUNNING.value,
                         trace_id, mission_id, run_id, actor, args)
        try:
            if dry_run:
                r = ToolResult.ok_result({"dry_run": True, "tool": name, "args": _redact(args)},
                                         trace_id=trace_id)
            else:
                ok, output, meta = self._execute_raw(name, args)
                r = _coerce_result(output, ok=ok, trace_id=trace_id, meta=meta)
        except TimeoutError as exc:
            r = ToolResult.error("TOOL_TIMEOUT", str(exc), retryable=True, trace_id=trace_id)
        except Exception as exc:
            r = ToolResult.error("TOOL_ERROR", f"{type(exc).__name__}: {exc}",
                                 retryable=_retryable(exc), trace_id=trace_id)
        finally:
            r.finished_at = _now_iso()
        r.duration_ms = int((time.time() - started) * 1000)
        self._emit(name, args, r, trace_id, mission_id, run_id, actor)
        return r

    def _gate(self, name: str, args: dict[str, Any]) -> Optional[ToolResult]:
        if self._policy is None or not callable(self._policy):
            return None
        decision = self._policy(name, args)
        if decision in (True, None, "allow", "ALLOW"):
            return None
        return ToolResult.error("POLICY_DENIED", f"policy denied tool '{name}'",
                                retryable=False, status="blocked",
                                next_action="blocked_by_policy")

    def _emit(self, name: str, args: dict[str, Any], result: ToolResult, trace_id: str,
              mission_id: Optional[str], run_id: Optional[str], actor: str) -> None:
        etype = EventType.COMMAND_SUCCEEDED.value if result.ok else EventType.COMMAND_FAILED.value
        status = CommandStatus.SUCCEEDED.value if result.ok else CommandStatus.FAILED.value
        self._emit_state(name, etype, status, trace_id, mission_id, run_id, actor,
                         args, result=result)

    def _emit_state(self, name: str, etype: str, status: str, trace_id: str,
                    mission_id: Optional[str], run_id: Optional[str], actor: str,
                    args: dict[str, Any], result: Optional[ToolResult] = None) -> None:
        env = EventEnvelope(
            trace_id=trace_id, mission_id=mission_id, run_id=run_id, actor=actor,
            source="agent", type=etype, command="tool.invoke", target=name,
            args_redacted=_redact(args), status=status,
            error_code=result.error_code if result else None,
            evidence_refs=result.evidence_refs if result else [],
            duration_ms=result.duration_ms if result else None,
        )
        self._bus.publish(env)


_gateway: Optional[ToolGateway] = None
_gateway_lock = threading.Lock()


def get_tool_gateway(registry: Any = None) -> ToolGateway:
    global _gateway
    with _gateway_lock:
        if _gateway is None:
            _gateway = ToolGateway(registry)
        return _gateway


def gateway_result_dict(res: 'ToolResult') -> dict[str, Any]:
    """Flatten a ToolResult into a plain dict shaped like the registry's raw tool output.

    This is the *one* place callers that previously read ``tool_registry.execute(...)``
    output (which returned the tool's own dict) get a compatible dict: on success the
    tool's ``output`` is returned verbatim; on failure a ``{error, error_code}`` dict.
    Centralizing this keeps invocation paths uniform and preserves the contract.
    """
    if res.ok:
        out = res.output
        return out if isinstance(out, dict) else {"result": out}
    return {"error": res.error_message or res.error_code or "tool failed",
            "error_code": res.error_code}


def tool_response(ok: bool, output: Any = None, **kw) -> ToolResult:
    return ToolResult(ok=ok, output=output, **kw)


# -- helpers -------------------------------------------------------------------
def _new_token() -> str:
    import uuid
    return str(uuid.uuid4())


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _redact(value: Any) -> Any:
    from ..contracts import redact
    return redact(value)


def _retryable(exc: Exception) -> bool:
    txt = f"{type(exc).__name__} {exc}".lower()
    transient = ("timeout", "rate", "429", "connection", "temporarily", "retry", "busy", "overload")
    return any(k in txt for k in transient)


def _coerce_result(output: Any, *, ok: bool = True, trace_id: Optional[str] = None,
                   meta: Optional[dict] = None) -> ToolResult:
    if isinstance(output, ToolResult):
        output.trace_id = output.trace_id or trace_id
        return output
    # Honest handling of an already-classified failure (output may be None).
    if ok is False and meta and meta.get("error_code") and (output is None or isinstance(output, dict)):
        return ToolResult.error(meta["error_code"],
                                str(meta.get("error_message") or meta.get("error") or "failed"),
                                retryable=bool(meta.get("retryable", False)),
                                trace_id=trace_id,
                                status=str(meta.get("status") or "error"),
                                next_action=meta.get("next_action"),
                                data=dict(meta.get("data") or {}))
    if isinstance(output, dict) and ("ok" in output or "error" in output) and "status" in output:
        try:
            return ToolResult(
                ok=bool(output.get("ok", ok)), status=str(output.get("status", "ok")),
                output=output.get("output"), error_code=output.get("error_code"),
                error_message=output.get("error_message"),
                evidence_refs=output.get("evidence_refs") or [],
                changed_resources=output.get("changed_resources") or [],
                trace_id=trace_id, retryable=bool(output.get("retryable", False)),
                next_action=output.get("next_action"),
            )
        except Exception:
            pass
    if isinstance(output, dict) and (output.get("error") or output.get("ok") is False):
        if meta and meta.get("error_code"):
            return ToolResult.error(meta["error_code"],
                                    str(meta.get("error_message") or output.get("error") or "failed"),
                                    retryable=bool(meta.get("retryable", False)), trace_id=trace_id,
                                    status=str(meta.get("status") or "error"),
                                    next_action=meta.get("next_action"),
                                    data=dict(meta.get("data") or {}))
        return ToolResult.error((output.get("error_code") or "TOOL_ERROR"),
                                str(output.get("error") or output.get("error_message") or "failed"),
                                retryable=_retryable_from_meta(meta), trace_id=trace_id)
    return ToolResult.ok_result(output, trace_id=trace_id)


def _retryable_from_meta(meta: Optional[dict]) -> bool:
    if not meta:
        return False
    return _retryable(Exception(str(meta.get("error", ""))))


def _typed_exception(exc: Exception) -> tuple[bool, Any, dict]:
    """Classify an exception raised during tool execution into a typed result."""
    err = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, TimeoutError):
        return False, None, {"error": err, "error_code": "TOOL_TIMEOUT",
                             "error_message": str(exc), "retryable": True}
    if isinstance(exc, PermissionError):
        return False, None, {"error": err, "error_code": "TOOL_BLOCKED",
                             "error_message": str(exc), "retryable": False}
    low = err.lower()
    if "timeout" in low or "timed out" in low or "took too long" in low:
        return False, None, {"error": err, "error_code": "TOOL_TIMEOUT",
                             "error_message": str(exc), "retryable": True}
    return False, None, {"error": err, "error_code": "TOOL_ERROR",
                         "error_message": str(exc), "retryable": _retryable(exc)}


def _classify_registry_result(raw: Any) -> tuple[bool, Any, dict]:
    """Classify a registry ``execute()`` return value.

    Handles the registry's own error shapes so the gateway can emit typed codes
    without reimplementing any tool logic.
    """
    if not isinstance(raw, dict):
        return True, raw, {"ok": True}
    # ``error`` may be absent, ``None``, or empty string — none of those is a failure.
    # Only a truthy error value means the tool failed. This matters because several
    # tools (e.g. sandbox_run) use ``"error": ""`` to mean "no error" in their shape.
    err = raw.get("error")
    if err:
        err = str(err)
        errmsg = raw.get("error_message") or raw.get("hint") or err
        permission = raw.get("permission") if isinstance(raw.get("permission"), dict) else {}
        approval_request = permission.get("approval_request") if isinstance(permission, dict) else None
        if approval_request or str(permission.get("decision", "")).lower() == "ask":
            return False, raw, {
                "error": err,
                "error_code": "APPROVAL_REQUIRED",
                "error_message": errmsg,
                "retryable": True,
                "status": "blocked",
                "next_action": "wait_for_approval",
                "data": {
                    "approval_request": approval_request,
                    "safety": permission.get("safety"),
                    "permission": permission,
                },
            }
        # Tool-not-found marker: registry returns available_sample/hint.
        if "Unknown tool" in err or "not found" in err.lower() or "available_sample" in raw:
            try:
                from ..capability_ledger import CapabilityEntry, get_capability_ledger

                get_capability_ledger().add_discovered(CapabilityEntry.create(
                    power=f"Tool capability: {str(raw.get('tool') or err).replace('Unknown tool', '').strip() or 'unknown'}",
                    use="Needed because a requested tool/capability was not registered",
                    risk="unknown until connector/tool is implemented and scoped",
                    needed_approval_setup=f"Implement/register the tool behind ToolGateway with permissions and tests. Reason: {errmsg}",
                    status="missing",
                    source="tool_gateway",
                ))
            except Exception:
                pass
            return False, raw, {"error": err, "error_code": "TOOL_NOT_FOUND",
                                "error_message": errmsg, "retryable": False}
        if "denied" in err.lower() or "DENY" in err.upper():
            return False, raw, {"error": err, "error_code": "POLICY_DENIED",
                                "error_message": errmsg, "retryable": False}
        # Classify transient/timeout-style failures so retry policy can act.
        if "timeout" in err.lower() or "timed out" in err.lower() or "took too long" in err.lower():
            return False, raw, {"error": err, "error_code": "TOOL_TIMEOUT",
                                "error_message": errmsg, "retryable": True}
        fc = raw.get("error_code") or "TOOL_ERROR"
        retryable = bool(raw.get("retryable", _retryable(Exception(err))))
        return False, raw, {"error": err, "error_code": fc,
                            "error_message": errmsg, "retryable": retryable}
    ok = bool(raw.get("ok", raw.get("success", True)))
    return ok, raw, {"ok": ok}
