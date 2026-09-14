"""Foundation: typed error taxonomy (core/errors.py) + gateway envelope."""

import pytest


def test_hermus_error_defaults_and_envelope():
    from core.errors import HermusError

    err = HermusError("boom")
    assert err.code == "internal"
    assert err.status == 500
    assert err.retryable is False
    assert str(err) == "boom"
    assert err.to_dict() == {"error": "internal", "message": "boom", "retryable": False, "details": {}}


def test_hermus_error_overrides():
    from core.errors import HermusError

    err = HermusError("nope", code="custom", status=418, retryable=True, details={"k": "v"})
    assert err.to_dict() == {"error": "custom", "message": "nope", "retryable": True, "details": {"k": "v"}}


def test_generic_subclass_status_codes():
    from core.errors import (
        AuthError,
        ForbiddenError,
        InvalidRequestError,
        MissionBlockedError,
        MissionError,
        NotFoundError,
        OperationTimeoutError,
        ProviderError,
        RateLimitError,
        ToolNotFoundError,
    )

    assert (AuthError("x").status, AuthError("x").code) == (401, "auth_error")
    assert ForbiddenError("x").status == 403
    assert NotFoundError("x").status == 404
    assert InvalidRequestError("x").status == 400
    assert RateLimitError("x").status == 429 and RateLimitError("x").retryable is True
    assert OperationTimeoutError("x").status == 504 and OperationTimeoutError("x").retryable is True
    assert ProviderError("x").status == 502 and ProviderError("x").retryable is True
    assert ToolNotFoundError("x").status == 404
    assert MissionError("x").status == 500
    assert MissionBlockedError("x").status == 409


def test_model_gateway_error_rebased_with_status_map():
    from core.contracts import FailureClass
    from core.errors import HermusError
    from core.models.gateway import ModelGatewayError

    err = ModelGatewayError("slow", failure_class=FailureClass.TIMEOUT.value, provider="ollama", model="m")
    assert isinstance(err, HermusError)
    assert isinstance(err, Exception)
    # Legacy attributes preserved for existing callers.
    assert err.failure_class == "timeout"
    assert err.error_code == "timeout"
    assert err.provider == "ollama" and err.model == "m"
    assert err.retryable is True
    # New envelope fields derived from the failure class.
    assert err.code == "timeout"
    assert err.status == 504
    assert err.to_dict()["details"] == {"provider": "ollama", "model": "m"}

    assert ModelGatewayError("r", failure_class=FailureClass.RATE_LIMIT.value).status == 429
    assert ModelGatewayError("a", failure_class=FailureClass.AUTH.value).status == 401
    assert ModelGatewayError("p", failure_class=FailureClass.POLICY_DENIED.value).status == 403
    assert ModelGatewayError("c", failure_class=FailureClass.CAPABILITY_MISMATCH.value).status == 400
    assert ModelGatewayError("u").status == 502


def test_compat_api_error_rebased():
    from core.errors import HermusError
    from core.openai_compat import CompatAPIError

    err = CompatAPIError("bad key", status_code=401)
    assert isinstance(err, HermusError)
    assert err.message == "bad key"
    assert err.is_auth_error is True
    assert err.is_rate_limit is False
    assert err.status == 401  # upstream status passes through

    rl = CompatAPIError("slow down", status_code=429)
    assert rl.code == "provider_rate_limited"
    assert rl.retryable is True
    assert rl.is_rate_limit is True

    conn = CompatAPIError("Connection error: refused", status_code=0)
    assert conn.status == 502  # no HTTP response -> Bad Gateway
    assert conn.retryable is True


def test_web_errors_rebased():
    from core.errors import HermusError
    from core.web.errors import (
        AllStrategiesFailedError,
        ResponseTooLargeError,
        SecurityBlockedError,
        StrategyUnavailableError,
        WebAcquisitionError,
    )

    base = WebAcquisitionError("x")
    assert isinstance(base, HermusError)
    assert base.code == "WEB_ERROR" and base.error_code == "WEB_ERROR"
    assert base.status == 502
    assert base.to_dict()["details"] == {"failure_class": "unknown"}

    assert SecurityBlockedError("no").status == 403
    assert SecurityBlockedError("no").retryable is False
    assert ResponseTooLargeError("big").status == 413
    strat = StrategyUnavailableError("missing dep", strategy="dynamic")
    assert strat.status == 501
    assert strat.strategy == "dynamic"
    assert AllStrategiesFailedError("all failed").retryable is True


def test_web_session_error_inherits_taxonomy():
    from core.errors import HermusError
    from core.web.sessions import WebSessionError

    err = WebSessionError("pinned")
    assert isinstance(err, HermusError)
    assert err.code == "WEB_SESSION_ERROR"


def test_plugin_error_rebased():
    from core.errors import HermusError
    from core.plugins import PluginError

    err = PluginError("plugin 'x' has no callable register(api)")
    assert isinstance(err, HermusError)
    assert err.code == "plugin_error"
    assert err.status == 500
    assert str(err) == "plugin 'x' has no callable register(api)"


def test_android_errors_keep_runtime_error_compat():
    from core.android.permissions import PermissionDenied
    from core.android.transport import AndroidUnavailable
    from core.errors import HermusError

    denied = PermissionDenied("no consent", op="tap", category="ui_control")
    assert isinstance(denied, HermusError)
    assert isinstance(denied, RuntimeError)
    assert denied.reason == "no consent"
    assert denied.op == "tap" and denied.category == "ui_control"
    assert denied.status == 403
    assert denied.to_dict()["details"] == {"op": "tap", "category": "ui_control"}

    unavail = AndroidUnavailable("no adb")
    assert isinstance(unavail, HermusError)
    assert isinstance(unavail, RuntimeError)
    assert unavail.reason == "no adb"
    assert unavail.status == 503


def test_rpc_error_stays_outside_taxonomy():
    """RpcError carries numeric JSON-RPC codes — a different domain, untouched."""
    from core.delegation import RpcError
    from core.errors import HermusError

    err = RpcError("worker not started", -32603)
    assert isinstance(err, RuntimeError)
    assert not isinstance(err, HermusError)
    assert err.code == -32603


def test_gateway_handlers_render_canonical_envelope():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from core.errors import HermusError, NotFoundError
    from gateway.gateway import register_error_handlers

    probe = FastAPI()

    @probe.get("/typed")
    def _typed():
        raise NotFoundError("no such run", details={"run_id": "r1"})

    @probe.get("/raw")
    def _raw():
        raise ValueError("kaboom-secret-stack")

    register_error_handlers(probe)
    client = TestClient(probe, raise_server_exceptions=False)

    typed = client.get("/typed")
    assert typed.status_code == 404
    assert typed.json() == {
        "success": False,
        "error": "not_found",
        "message": "no such run",
        "retryable": False,
        "details": {"run_id": "r1"},
    }

    raw = client.get("/raw")
    assert raw.status_code == 500
    body = raw.json()
    assert body["success"] is False
    assert body["error"] == "internal"
    # Internals never leak into the 500 body.
    assert "kaboom" not in body["message"]

    with pytest.raises(NotFoundError):
        raise NotFoundError("still an exception", details={})
    assert issubclass(NotFoundError, HermusError)
