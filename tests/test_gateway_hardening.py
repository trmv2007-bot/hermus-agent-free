"""Phase 4 gateway hardening: envelope, request context, rate limit, probes.

These cover the cross-cutting HTTP layer added in ``gateway/envelope.py``,
``gateway/middleware.py`` and ``gateway/lifecycle.py``:

* every 4xx/5xx JSON response leaves with the canonical envelope keys —
  including FastAPI's own ``{"detail": ...}`` and the legacy
  ``{"error": ...}`` sites, without losing the keys clients already read;
* every request is correlated (``X-Request-ID`` echoed, log line emitted);
* the rate limiter is opt-in, returns a canonical 429 with ``Retry-After``,
  and never touches exempt probe paths;
* liveness and readiness are distinct, and readiness is *derived* — it is 503
  while draining rather than a hardcoded ``ok``.
"""

from __future__ import annotations

import importlib
import os

import pytest

os.environ.setdefault("HERMUS_NO_DOTENV", "1")

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gateway import envelope, lifecycle, middleware  # noqa: E402
from gateway.gateway import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate_lifecycle():
    """Lifecycle state is process-wide (like the queue); snapshot + restore."""
    s = lifecycle.state
    saved = (s.started, s.draining, s.drain_started_at, s.shutdown_reason)
    yield
    s.started, s.draining, s.drain_started_at, s.shutdown_reason = saved


@pytest.fixture()
def access_log():
    """Capture ``hermus.*`` records.

    ``core.log`` sets ``propagate = False`` on the ``hermus`` logger, so
    pytest's caplog (a root handler) never sees them — attach directly to the
    logger the gateway actually writes to.
    """
    import logging

    logger = logging.getLogger("hermus")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.INFO)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def _clean_rate_limits():
    """Rate-limit buckets are process-wide; never leak between tests."""
    middleware.reset_rate_limits()
    saved = os.environ.pop("HERMUS_RATE_LIMIT_PER_MINUTE", None)
    saved_exempt = os.environ.pop("HERMUS_RATE_LIMIT_EXEMPT", None)
    yield
    middleware.reset_rate_limits()
    os.environ.pop("HERMUS_RATE_LIMIT_PER_MINUTE", None)
    os.environ.pop("HERMUS_RATE_LIMIT_EXEMPT", None)
    if saved is not None:
        os.environ["HERMUS_RATE_LIMIT_PER_MINUTE"] = saved
    if saved_exempt is not None:
        os.environ["HERMUS_RATE_LIMIT_EXEMPT"] = saved_exempt


# ---------------------------------------------------------------------------
# envelope helpers
# ---------------------------------------------------------------------------
def test_ok_preserves_payload_and_flags_success():
    body = envelope.ok({"job_id": "j1", "status": "queued"})
    assert body["success"] is True
    assert body["job_id"] == "j1"
    assert body["status"] == "queued"


def test_ok_accepts_extra_keywords():
    assert envelope.ok(count=3) == {"success": True, "count": 3}


def test_fail_renders_canonical_body_and_status():
    resp = envelope.fail("job not found", status=404)
    assert resp.status_code == 404
    body = resp.body and __import__("json").loads(resp.body)
    assert body["success"] is False
    assert body["error"] == body["code"]
    assert "not found" in body["message"].lower()
    assert body["retryable"] is False
    assert body["details"] == {}


def test_fail_marks_5xx_and_429_retryable():
    assert __import__("json").loads(envelope.fail("boom", status=500).body)["retryable"] is False
    assert __import__("json").loads(envelope.fail("slow down", status=429).body)["retryable"] is True
    assert __import__("json").loads(envelope.fail("down", status=503).body)["retryable"] is True


def test_fail_with_explicit_code_and_details():
    resp = envelope.fail("validation_error", "field 'text' is required", status=422, details={"field": "text"})
    body = __import__("json").loads(resp.body)
    assert body["code"] == "validation_error"
    assert body["message"] == "field 'text' is required"
    assert body["details"] == {"field": "text"}


def test_normalize_is_additive_for_legacy_error_bodies():
    """A legacy ``{"error": "..."}`` must keep working for existing clients."""
    body = envelope.normalize_error_body({"error": "no provider configured"}, 400)
    assert body["error"] == "no provider configured"
    assert body["success"] is False
    assert body["code"]
    assert body["message"]
    assert body["retryable"] is False


def test_normalize_keeps_structured_error_under_details():
    body = envelope.normalize_error_body({"error": {"provider": "openai", "reason": "429"}}, 429)
    assert body["success"] is False
    assert body["code"] == "rate_limited"
    assert body["retryable"] is True
    assert body["details"]["error"] == {"provider": "openai", "reason": "429"}


def test_normalize_wraps_non_object_bodies():
    body = envelope.normalize_error_body("nope", 500)
    assert body["success"] is False
    assert body["code"] == "internal"
    assert body["details"]["body"] == "nope"


def test_normalize_is_idempotent():
    once = envelope.normalize_error_body({"error": "bad"}, 400)
    twice = envelope.normalize_error_body(once, 400)
    assert once == twice


# ---------------------------------------------------------------------------
# envelope applied by middleware to real responses
# ---------------------------------------------------------------------------
def test_fastapi_404_detail_is_normalized_but_preserved(client):
    resp = client.get("/definitely-not-a-route")
    assert resp.status_code == 404
    body = resp.json()
    # Canonical keys added...
    assert body["success"] is False
    assert body["code"] == "not_found"
    assert body["error"]
    assert "retryable" in body
    # ...and the key FastAPI clients already read is untouched.
    assert body["detail"] == "Not Found"


def test_legacy_json_error_site_is_normalized(client):
    """A route returning a hand-rolled error JSON keeps its `error` string."""
    probe = FastAPI()
    from gateway.middleware import ErrorEnvelopeMiddleware

    probe.add_middleware(ErrorEnvelopeMiddleware)

    @probe.get("/legacy")
    def legacy():
        return JSONResponse(status_code=400, content={"error": "no provider configured"})

    with TestClient(probe) as c:
        body = c.get("/legacy").json()
    assert body["error"] == "no provider configured"
    assert body["success"] is False
    assert body["code"] == "no_provider_configured"


def test_success_bodies_are_left_alone(client):
    """2xx payloads keep their exact shape — no envelope key is injected."""
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert "code" not in body


def test_non_json_errors_pass_through_untouched():
    probe = FastAPI()
    from gateway.middleware import ErrorEnvelopeMiddleware

    probe.add_middleware(ErrorEnvelopeMiddleware)

    @probe.get("/html-error")
    def html_error():
        from fastapi.responses import HTMLResponse

        return HTMLResponse("<h1>nope</h1>", status_code=500)

    with TestClient(probe) as c:
        resp = c.get("/html-error")
    assert resp.status_code == 500
    assert resp.text == "<h1>nope</h1>"


# ---------------------------------------------------------------------------
# request context
# ---------------------------------------------------------------------------
def test_request_id_is_generated_and_echoed(client):
    resp = client.get("/healthz")
    rid = resp.headers.get("x-request-id")
    assert rid and rid.startswith("req_")


def test_client_supplied_request_id_is_honoured(client):
    resp = client.get("/healthz", headers={"X-Request-ID": "client-abc"})
    assert resp.headers["x-request-id"] == "client-abc"


def test_unsafe_request_id_is_replaced(client):
    """A header is untrusted input: it must not be echoed back verbatim."""
    resp = client.get("/healthz", headers={"X-Request-ID": "bad id\r\nX-Injected: 1"})
    rid = resp.headers["x-request-id"]
    assert rid.startswith("req_")
    assert "\r" not in rid and "\n" not in rid


def test_response_time_header_present(client):
    resp = client.get("/healthz")
    assert resp.headers.get("x-response-time", "").endswith("ms")


def test_access_log_carries_the_request_id(client, access_log):
    resp = client.get("/healthz", headers={"X-Request-ID": "correlate-me"})
    rid = resp.headers["x-request-id"]
    messages = [r.getMessage() for r in access_log if r.name.endswith("gateway.access")]
    assert any("correlate-me" in m for m in messages), messages
    # The structured filter also binds the id onto the record itself, so any
    # downstream log line from this request is correlatable.
    assert any(getattr(r, "request_id", "") == rid for r in access_log)


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------
def test_rate_limit_disabled_by_default(client):
    assert middleware.RateLimitMiddleware.limit_from_env() == 0
    for _ in range(25):
        assert client.get("/healthz").status_code == 200


def test_rate_limit_rejects_with_canonical_429(client):
    os.environ["HERMUS_RATE_LIMIT_PER_MINUTE"] = "5"
    middleware.reset_rate_limits()
    codes = [client.get("/platforms").status_code for _ in range(8)]
    assert codes[:5] == [200] * 5
    assert set(codes[5:]) == {429}

    limited = client.get("/platforms")
    body = limited.json()
    assert body["success"] is False
    assert body["code"] == "rate_limited"
    assert body["retryable"] is True
    assert limited.headers.get("retry-after")
    assert body["details"]["limit_per_minute"] == 5


def test_rate_limit_exempts_probe_paths(client):
    os.environ["HERMUS_RATE_LIMIT_PER_MINUTE"] = "1"
    middleware.reset_rate_limits()
    assert client.get("/platforms").status_code == 200
    assert client.get("/platforms").status_code == 429
    # Probes must never be limited: a supervisor hammering them is normal, and
    # a 429 on /readyz would make the gateway look dead to its own supervisor.
    for _ in range(10):
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code in (200, 503)
        assert client.get("/api/status").status_code == 200


def test_rate_limit_bucket_is_per_token(client):
    os.environ["HERMUS_RATE_LIMIT_PER_MINUTE"] = "1"
    middleware.reset_rate_limits()
    assert client.get("/platforms", headers={"X-Hermus-Token": "one"}).status_code == 200
    assert client.get("/platforms", headers={"X-Hermus-Token": "one"}).status_code == 429
    # A different token is a different bucket (shared NAT must not starve it).
    assert client.get("/platforms", headers={"X-Hermus-Token": "two"}).status_code == 200


def test_rate_limit_key_never_contains_the_token():
    """The bucket key is logged; it must not leak the credential."""
    os.environ["HERMUS_RATE_LIMIT_PER_MINUTE"] = "1"
    middleware.reset_rate_limits()
    with TestClient(app) as c:
        c.get("/platforms", headers={"X-Hermus-Token": "supersecret"})
        c.get("/platforms", headers={"X-Hermus-Token": "supersecret"})
    assert "supersecret" not in " ".join(f"{k}" for k in middleware._BUCKETS._hits)


# ---------------------------------------------------------------------------
# health probes
# ---------------------------------------------------------------------------
def test_liveness_is_always_ok(client):
    for path in ("/healthz", "/livez"):
        body = client.get(path).json()
        assert body["status"] == "ok"
        assert body["uptime"] >= 0


def test_readiness_reports_real_state(client):
    body = client.get("/readyz").json()
    assert body["ready"] is True
    assert body["draining"] is False
    assert body["queue"] in ("running", "disabled")
    assert body["reasons"] == []


def test_readiness_is_503_while_draining(client):
    lifecycle.state.begin_drain("test")
    try:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        body = resp.json()
        assert body["success"] is False
        assert body["code"] == "not_ready"
        assert body["retryable"] is True
        assert any("draining" in r for r in body["details"]["reasons"])
        # The control-room readiness pill renders `message` and
        # `details.reasons` (tests/test_control_room_ux.py pins the UI side),
        # so both must stay populated — not just the status code.
        assert "draining" in body["message"]
        # Liveness stays up: the process is serving, just not taking traffic.
        assert client.get("/healthz").json()["status"] == "ok"
    finally:
        lifecycle.state.draining = False
        lifecycle.state.drain_started_at = None
        lifecycle.state.shutdown_reason = ""


def test_readiness_reports_queue_not_started(client, monkeypatch):
    """Readiness is derived from the queue too — never a hardcoded ok."""
    assert lifecycle.readiness()[0] is True  # live app: started + queue running

    monkeypatch.setattr(lifecycle.state, "started", False)
    ready, detail = lifecycle.readiness()
    assert ready is False
    assert any("lifespan" in r for r in detail["reasons"])


def test_restart_clears_the_drain_flag(client):
    """A second lifespan start must not inherit the previous shutdown's drain.

    Real bug this pins: without it, any in-process restart (tests, embedded
    reload) reported 'not ready' forever after the first shutdown.
    """
    state = lifecycle.state
    state.begin_drain("shutdown")
    assert lifecycle.readiness()[0] is False

    state.mark_started()
    assert state.draining is False
    assert state.drain_started_at is None
    assert state.shutdown_reason == ""
    assert lifecycle.readiness()[0] is True


def test_drain_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("HERMUS_DRAIN_TIMEOUT", "7.5")
    assert lifecycle.drain_timeout_seconds() == 7.5
    monkeypatch.setenv("HERMUS_DRAIN_TIMEOUT", "not-a-number")
    assert lifecycle.drain_timeout_seconds() > 0


def test_queue_refuses_new_work_while_draining(tmp_path):
    """A drain must actually stop intake — not just advertise 503 on /readyz.

    The flag is per-queue (not process-global) on purpose: a restarted queue
    accepts work again, so an in-process restart never latches 'unavailable'.
    """
    import asyncio

    from core.errors import UnavailableError
    from gateway.queue import JobQueue

    async def scenario():
        q = JobQueue(
            workers=1,
            maxsize=5,
            default_timeout=5,
            persist=str(tmp_path / "jobs.jsonl"),
        )
        q.register("noop", lambda ctx: {"ok": True})
        await q.start()
        assert q.submit("noop", {}).id

        await q.stop(drain_timeout=0.1)
        with pytest.raises(UnavailableError) as excinfo:
            q.submit("noop", {})
        assert excinfo.value.status == 503
        assert excinfo.value.code == "unavailable"
        assert excinfo.value.retryable is True

        # Restart clears the drain: no global latch.
        await q.start()
        assert q.submit("noop", {}).id
        await q.stop(drain_timeout=0.1)

    asyncio.run(scenario())


def test_readiness_reports_queue_draining(client, monkeypatch):
    from gateway.queue import job_queue

    monkeypatch.setattr(job_queue, "_draining", True)
    ready, detail = lifecycle.readiness()
    assert ready is False
    assert detail["queue"] == "draining"
    assert any("draining" in r for r in detail["reasons"])


# ---------------------------------------------------------------------------
# websocket auth is single-sourced
# ---------------------------------------------------------------------------
def test_ws_auth_helper_open_when_no_token(monkeypatch):
    from gateway.context import ws_token_ok

    monkeypatch.delenv("HERMUS_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr("core.config.config.gateway_api_token", None, raising=False)

    class _WS:
        query_params = {}
        headers = {}

    assert ws_token_ok(_WS()) is True


def test_ws_auth_helper_requires_matching_token(monkeypatch):
    from gateway.context import ws_token_ok

    monkeypatch.setenv("HERMUS_GATEWAY_TOKEN", "s3cret")
    monkeypatch.setattr("core.config.config.gateway_api_token", None, raising=False)

    class _WS:
        def __init__(self, params=None, headers=None):
            self.query_params = params or {}
            self.headers = headers or {}

    assert ws_token_ok(_WS()) is False
    assert ws_token_ok(_WS({"token": "wrong"})) is False
    assert ws_token_ok(_WS({"token": "s3cret"})) is True
    assert ws_token_ok(_WS(headers={"X-Hermus-Token": "s3cret"})) is True


def test_ws_endpoints_use_the_shared_helper():
    """No WS endpoint may re-implement the token check by hand."""
    for mod in ("gateway.routes_computer", "gateway.routes_speech"):
        src = importlib.import_module(mod).__loader__.get_source(mod)  # type: ignore[union-attr]
        assert src is not None
        assert "ws_token_ok" in src
        assert "code=1008" in src


# ---------------------------------------------------------------------------
# structural: the middleware stack is actually installed
# ---------------------------------------------------------------------------
def test_middleware_stack_is_wired():
    names = [m.cls.__name__ for m in app.user_middleware]
    for expected in ("ErrorEnvelopeMiddleware", "RateLimitMiddleware", "RequestContextMiddleware"):
        assert expected in names, f"{expected} missing from {names}"


def test_envelope_middleware_runs_inside_request_context():
    """Context must be outside the limiter so 429s are still correlated."""
    names = [m.cls.__name__ for m in app.user_middleware]
    # Starlette applies user_middleware[0] outermost.
    assert names.index("RequestContextMiddleware") < names.index("RateLimitMiddleware")
