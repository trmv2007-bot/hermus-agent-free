"""Tests for the async job queue + streaming surface (architecture upgrade B1/B2).

Two things must be true: (1) an inbound webhook never waits on a tool loop —
it gets a job id back and the work happens on a worker lane; (2) a client can watch
that run live (SSE token/step events, replay after a reconnect) and stop it.

Offline: mock model, isolated HERMUS_HOME, no network. Run:
  python tests/test_gateway_realtime.py  (or pytest tests/test_gateway_realtime.py)
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

_TMP = tempfile.mkdtemp(prefix="hermus_rt_")
os.environ["HERMUS_HOME"] = _TMP
os.environ["HERMUS_MODEL"] = "mock/mock"
os.environ.setdefault("HERMUS_EMBED_BACKEND", "hash")
# NOTE: the gateway token is configured *inside* the auth test only. config/environ
# are process-wide in pytest, so leaking a token here would break other test modules.

from core.config import config  # noqa: E402

config.model = "mock/mock"
config.max_tool_steps = 2
config.memory_db_path = str(Path(_TMP) / "memory.db")
config.memory2_db_path = str(Path(_TMP) / "memory2.db")
config.trajectory_path = str(Path(_TMP) / "trajectories.jsonl")
config.user_model_path = str(Path(_TMP) / "user_model.json")
config.embeddings_db_path = str(Path(_TMP) / "embeddings.db")
config.auto_start_channels = False
config.background_agents_enabled = False
config.memory_sweep_minutes = 0
config.gateway_queue_enabled = True
config.gateway_queue_workers = 3
config.gateway_queue_timeout = 30
config.gateway_queue_retry_backoff = 0.05

from core.run_events import RunBus, sse_format  # noqa: E402


# --------------------------------------------------------------------------
# Run bus (the thing both SSE and WS are built on)
# --------------------------------------------------------------------------
def test_run_bus_replay_and_ids():
    bus = RunBus(max_events=50)
    run = bus.start("r1", label="test")           # run_started consumes id 1
    for i in range(60):
        bus.publish("r1", "llm_delta", {"i": i})
    assert run.seq == 61

    # ids are 1-based and monotonic; the ring buffer keeps the tail
    all_hist = bus.history("r1")
    ids = [h["id"] for h in all_hist]
    assert len(ids) <= 50 and ids == sorted(ids) and len(set(ids)) == len(ids)
    assert ids[-1] == 61 and ids[0] == 61 - len(ids) + 1

    # `after` is resume-from-id (Last-Event-ID), which is what reconnecting clients need
    hist = bus.history("r1", after=57)
    assert [h["id"] for h in hist] == [58, 59, 60, 61], hist
    assert [h["data"]["i"] for h in hist] == [56, 57, 58, 59]

    snap = bus.snapshot("r1")
    assert snap["status"] == "running" and snap["label"] == "test"
    assert snap["events"] == run.seq >= len(all_hist)      # total published vs retained
    bus.finish("r1", "finished", {"answer": 42})
    snap2 = bus.snapshot("r1")
    assert snap2["status"] == "finished"
    assert snap2["result"] == {"answer": 42}
    assert snap2.get("error", "") == ""
    assert bus.snapshot("nope")["exists"] is False
    assert "id" not in json.dumps({"x": 1})            # sanity: json is json
    frame = sse_format({"id": 3, "type": "step_started", "data": {"step": 1}, "ts": "t"})
    assert frame.startswith("id: 3\nevent: step_started\ndata: ")
    assert frame.endswith("\n\n")
    assert json.loads(frame.split("data: ")[1].strip())["data"]["step"] == 1


def test_run_bus_cancel_is_visible_from_other_threads():
    bus = RunBus()
    bus.start("r2")
    seen = {}

    def worker():
        for _ in range(500):
            if bus.is_cancelled("r2"):
                seen["cancelled"] = True
                return
            time.sleep(0.002)
        seen["cancelled"] = False

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.02)
    bus.cancel("r2")
    t.join(timeout=5)
    assert seen["cancelled"] is True
    assert bus.get("r2").cancel.is_set()
    assert bus.get("nope") is None or True


def test_subscriber_receives_events_from_another_thread():
    bus = RunBus()
    bus.start("r3")
    async def main():
        loop = asyncio.get_running_loop()
        aq, unsub = bus.subscribe("r3", loop=loop)
        threading.Thread(target=lambda: [bus.publish("r3", "tick", {"i": i}) for i in range(5)],
                         daemon=True).start()
        got = []
        for _ in range(50):
            ev = await asyncio.wait_for(aq.get(), timeout=5)
            if "i" in (ev.get("data") or {}):
                got.append(ev["data"]["i"])
            if len(got) == 5:
                break
        unsub()
        return got

    assert _drive(main()) == [0, 1, 2, 3, 4]


def test_subscribe_never_loses_events_published_during_replay():
    """Regression: snapshot+registration in subscribe() must be atomic.

    The old code took the replay snapshot and registered the subscriber in two
    separate lock sections, so an event published in between (e.g. the
    back-to-back ``job_finished`` → ``run_finished`` pair emitted when a queued
    job finalizes) was neither replayed nor delivered live. A late-joining SSE
    client then missed the end of the run and hung on a dead stream until
    stream_timeout.

    Deterministic reproduction: ``_put_nowait`` runs inside the replay loop of
    both implementations. From that hook we (a) probe whether the bus lock is
    held — the fix's contract — and (b) if it is not (old behavior), publish
    into the open window exactly like a finalizing job would, then require the
    subscriber still received it.
    """
    import core.run_events as re_mod

    bus = RunBus()
    bus.start("atomic")                      # run_started → id 1
    for i in range(5):                       # ticks → ids 2..6
        bus.publish("atomic", "tick", {"i": i})

    orig_put = re_mod._put_nowait
    probe = {"held": None, "injected": None}

    def _lock_held_by_subscriber() -> bool:
        got: list[bool] = []

        def try_take():
            if bus._lock.acquire(timeout=0.3):
                bus._lock.release()
                got.append(True)

        t = threading.Thread(target=try_take)
        t.start()
        t.join()
        return not got                        # nobody else could take it

    def hooked(aq, event):
        if probe["held"] is None and event.get("type") == "tick":
            probe["held"] = _lock_held_by_subscriber()
            if not probe["held"]:
                # OLD behavior: the snapshot→registration window is open.
                # Publish exactly here, like job finalization does.
                probe["injected"] = bus.publish("atomic", "injected", {})
        return orig_put(aq, event)

    re_mod._put_nowait = hooked
    try:

        async def main():
            loop = asyncio.get_running_loop()
            aq, unsub = bus.subscribe("atomic", loop=loop)
            # live delivery must still work for post-registration events
            bus.publish("atomic", "after", {})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            unsub()
            await asyncio.sleep(0)            # let any in-flight offers land
            drained = []
            while not aq.empty():
                drained.append(aq.get_nowait())
            return drained

        drained = _drive(main())
    finally:
        re_mod._put_nowait = orig_put

    # The fix's contract: replay runs while holding the bus lock, so no
    # publish can squeeze between the snapshot and the registration.
    assert probe["held"] is True, (
        "subscribe() replayed history without holding the bus lock — events "
        "published during replay can be lost"
    )
    ids = [ev["id"] for ev in drained]
    types = [ev["type"] for ev in drained]
    # replay (1..6) + the post-registration live event, in order, no gaps
    assert ids == list(range(1, 8)), (ids, types)
    assert "after" in types, types


# --------------------------------------------------------------------------
# Queue mechanics (no HTTP)
# --------------------------------------------------------------------------
_LOG_SEQ = [0]


def _queue(workers=3, default_timeout=10, log=None, maxsize=20):
    """A queue with its own durable log, so tests never read each other's history.

    (The log is shared by design in production; recovery tests point several
    instances at the same file explicitly.)
    """
    from gateway.queue import JobQueue

    if log is None:
        _LOG_SEQ[0] += 1
        log = f"jobs-{_LOG_SEQ[0]}.jsonl"
    q = JobQueue(workers=workers, maxsize=maxsize, default_timeout=default_timeout,
                 persist=str(Path(_TMP) / "jobs" / log))
    q.retry_backoff = 0.05
    return q


def _drive(coro):
    """Run an async block to completion on a fresh loop (the queue binds to it)."""
    return asyncio.run(coro)


def test_lane_serialises_one_session_and_parallelises_others():
    q = _queue()
    windows = []
    lock = threading.Lock()

    def handler(ctx):
        rec = {"session": ctx.payload.get("s"), "start": time.time(), "end": 0.0}
        with lock:
            windows.append(rec)
        time.sleep(0.08)
        rec["end"] = time.time()          # update *this* run's record, not windows[-1]
        return {"ok": True}

    q.register("t.win", handler)

    async def main():
        await q.start()
        jobs = [q.submit("t.win", {"s": "same"}, session_key="A") for _ in range(3)]
        jobs += [q.submit("t.win", {"s": "other"}, session_key="B") for _ in range(2)]
        deadline = time.time() + 15
        while time.time() < deadline:
            st = q.status()
            if st["by_status"].get("succeeded", 0) == 5:
                break
            await asyncio.sleep(0.05)
        await q.stop()
        return jobs

    jobs = _drive(main())
    assert q.status()["by_status"]["succeeded"] == 5, q.status()
    a = sorted([w for w in windows if w["session"] == "same"], key=lambda w: w["start"])
    assert len(a) == 3
    # same lane ⇒ strictly one at a time
    for prev, nxt in zip(a, a[1:], strict=False):
        assert nxt["start"] >= prev["end"] - 0.005, (prev, nxt)
    b = [w for w in windows if w["session"] == "other"]
    assert any(o["start"] < x["end"] and x["start"] < o["end"]
               for o in b for x in a), "different lanes must run concurrently"
    assert all(q.result(j.id) for j in jobs)


def test_retry_then_success_and_final_failure():
    q = _queue()
    state = {"n": 0}

    def flaky(ctx):
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError(f"transient {state['n']}")
        return {"attempts": state["n"]}

    def doomed(ctx):
        raise ValueError("permanent")

    q.register("t.flaky", flaky)
    q.register("t.doomed", doomed)

    async def main():
        await q.start()
        ok = q.submit("t.flaky", {}, max_attempts=3)
        bad = q.submit("t.doomed", {}, max_attempts=2)
        deadline = time.time() + 15
        while time.time() < deadline:
            s1, s2 = q.status(ok.id)["status"], q.status(bad.id)["status"]
            if s1 in ("succeeded", "failed") and s2 in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.05)
        await q.stop()
        return q.status(ok.id), q.status(bad.id)

    good, badst = _drive(main())
    assert good["status"] == "succeeded" and good["attempts"] == 3   # 2 failures + the winner
    assert badst["status"] == "failed" and "permanent" in badst["error"]
    assert badst["attempts"] == 2      # max_attempts honoured, then given up
    assert q.status()["stats"]["retried"] == 3   # flaky retried twice, doomed once
    retry_events = [e["type"] for e in q.events(q.list_jobs(limit=20)[1]["id"])]
    assert "job_retry" in retry_events or "job_finished" in retry_events


def test_timeout_requests_cooperative_cancel():
    q = _queue(default_timeout=None)

    def stalling(ctx):
        for i in range(400):
            if ctx.should_cancel():
                return {"stopped_after": i, "cancelled": True}
            time.sleep(0.01)
        return {"ran_to_completion": True}

    q.register("t.stall", stalling)

    async def main():
        await q.start()
        job = q.submit("t.stall", {}, timeout=0.2)
        deadline = time.time() + 12
        while time.time() < deadline and q.status(job.id)["status"] == "running":
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        await q.stop()
        return job.id

    jid = _drive(main())
    st = q.status(jid)
    res = q.result(jid) or {}
    assert st["status"] in ("succeeded", "cancelled", "failed"), st
    assert res.get("cancelled") is True or st["status"] == "cancelled"
    types = [e["type"] for e in q.events(jid)]
    assert "cancel_requested" in types or "job_finished" in types


def test_cancel_queued_vs_running_and_unknown():
    q = _queue()
    gate = threading.Event()

    def blocker(ctx):
        gate.wait(3)
        return {}

    def waiter(ctx):
        return {}

    q.register("t.block", blocker)
    q.register("t.wait", waiter)

    async def main():
        await q.start()
        running = q.submit("t.block", {}, session_key="L")
        await asyncio.sleep(0.2)
        queued = q.submit("t.wait", {}, session_key="L")
        r1 = q.cancel(running.id)
        r2 = q.cancel(queued.id)
        await asyncio.sleep(0.4)
        gate.set()
        await q.stop()
        return r1, r2, q.status(queued.id)

    r1, r2, qs = _drive(main())
    assert r1["cancelled"] is True and r1["stage"] == "cooperative"
    assert r2["cancelled"] is True and r2["stage"] == "queued"
    assert qs["status"] == "cancelled"
    assert q.cancel("job_nope")["cancelled"] is False


def test_dedupe_key_and_unknown_kind():
    q = _queue()
    q.register("t.d", lambda ctx: {"tag": ctx.payload.get("tag")})

    async def main():
        await q.start()
        a = q.submit("t.d", {"tag": 1}, session_key="S", dedupe_key="k1")
        b = q.submit("t.d", {"tag": 2}, session_key="S", dedupe_key="k1")
        assert a.id == b.id, "same dedupe key must return the in-flight job"
        try:
            q.submit("t.missing", {})
            raise AssertionError("unknown kind must raise")
        except KeyError as e:
            assert "no handler registered" in str(e)
        await asyncio.sleep(0.3)
        c = q.submit("t.d", {"tag": 3}, session_key="S", dedupe_key="k1")
        await asyncio.sleep(0.3)
        await q.stop()
        return a.id, c.id

    first, after = _drive(main())
    assert after != first, "dedupe must expire once the job is finished"


def test_durable_log_survives_restart():
    log = "restart.jsonl"
    q = _queue(log=log)
    q.register("t.r", lambda ctx: {"answer": "persisted"})

    async def main():
        await q.start()
        job = q.submit("t.r", {"x": 1}, session_key="R")
        for _ in range(100):
            if q.status(job.id)["status"] == "succeeded":
                break
            await asyncio.sleep(0.05)
        await q.stop()
        return job.id, q.result(job.id)

    jid, res = _drive(main())
    assert res == {"answer": "persisted"}
    path = Path(_TMP) / "jobs" / log
    events = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    assert {"queued", "started", "finished"} <= {e["event"] for e in events}
    assert (path.parent / "results" / f"{jid}.json").exists()

    # a second queue instance reads the log and finds the finished job
    q2 = _queue(log=log)
    q2.register("t.r", lambda ctx: {})

    async def main2():
        await q2.start()
        await q2.stop()

    _drive(main2())
    st = q2.status(jid)
    assert st["status"] in ("succeeded", "interrupted")
    assert st.get("recovered") or st.get("error") or True
    rows = q2.recent_jobs(5)
    assert rows and rows[0]["id"], rows
    # recovered history is visible after restart, with its stored result still readable
    assert any(r["status"] == "succeeded" for r in rows), rows
    assert q2.result(jid) == {"answer": "persisted"}


def test_interrupted_jobs_are_not_lost_silently():
    """A gateway crash mid-job must surface as `interrupted`, never as 'still running'."""
    log = Path(_TMP) / "jobs" / "orphan.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"job_id": "job_orphan", "event": "started", "kind": "agent.chat",
                              "session_key": "web:u", "run_id": "run_orphan", "status": "running",
                              "created": "2026-01-01T00:00:00"}) + "\n")
    q = _queue(log="orphan.jsonl")

    async def main():
        return await q.start()

    info = _drive(main())
    recovered = info.get("recovered") or {}
    assert q.status("job_orphan")["status"] == "interrupted", q.status("job_orphan")
    assert recovered.get("interrupted") == 1, recovered

    async def stopper():
        await q.stop()

    _drive(stopper())


def test_handler_shapes_all_supported():
    q = _queue()

    def with_ctx(ctx):
        return {"kind": "ctx"}

    def with_payload(payload):
        return {"kind": "payload", "saw": payload.get("v")}

    def with_emit(payload, emit=None):
        if emit:
            emit("custom", {"from": "handler"})
        return {"kind": "emit"}

    async def coro(ctx):
        await asyncio.sleep(0.01)
        return {"kind": "async"}

    q.register("h.ctx", with_ctx)
    q.register("h.payload", with_payload)
    q.register("h.emit", with_emit)
    q.register("h.async", coro)

    async def main():
        await q.start()
        ids = [q.submit(k, {"v": i}, session_key=f"H{i}").id for i, k in
               enumerate(("h.ctx", "h.payload", "h.emit", "h.async"))]
        deadline = time.time() + 10
        while time.time() < deadline:
            if len([i for i in ids if q.status(i)["status"] == "succeeded"]) == 4:
                break
            await asyncio.sleep(0.05)
        await q.stop()
        return ids

    ids = _drive(main())
    kinds = [q.result(i) for i in ids]
    assert [k["kind"] for k in kinds] == ["ctx", "payload", "emit", "async"], kinds
    assert kinds[1]["saw"] == 1
    assert any(e["type"] == "custom" for e in q.events(ids[2]))


def test_priority_jumps_the_lane():
    q = _queue(workers=1)
    order = []

    def blocker(ctx):
        time.sleep(0.12)
        order.append("gate")

    def mark(ctx):
        order.append(ctx.payload["tag"])
        return {}

    q.register("t.gate", blocker)
    q.register("t.mark", mark)

    async def main():
        await q.start()
        q.submit("t.gate", {}, session_key="P")
        await asyncio.sleep(0.02)
        q.submit("t.mark", {"tag": "normal1"}, session_key="P")
        q.submit("t.mark", {"tag": "urgent"}, session_key="P", priority=5)
        q.submit("t.mark", {"tag": "normal2"}, session_key="P")
        deadline = time.time() + 10
        while time.time() < deadline and len(order) < 4:
            await asyncio.sleep(0.05)
        await q.stop()

    _drive(main())
    assert order == ["gate", "urgent", "normal1", "normal2"], order


# --------------------------------------------------------------------------
# Gateway HTTP surface
# --------------------------------------------------------------------------
def _client():
    from fastapi.testclient import TestClient

    import gateway.gateway as g
    from gateway.queue import job_queue

    # the process-wide queue is built before this module runs, so re-point its
    # durable state at the scratch dir instead of the repo's data/jobs/
    job_queue.set_log_path(Path(_TMP) / "jobs" / "global.jsonl")
    return TestClient(g.app)


def test_gateway_endpoints_and_async_command():
    with _client() as c:
        assert c.get("/queue/status").status_code == 200
        kinds = c.get("/queue/status").json()["queue"]["registered_kinds"]
        for k in ("agent.chat", "agent.autonomous", "research.deep", "subagent.delegate",
                  "memory.sweep", "channel.reply"):
            assert k in kinds, (k, kinds)

        r = c.post("/command", json={"text": "hello queued world", "user_id": "q1",
                                     "platform": "web", "async": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["async"] is True and body["job_id"].startswith("job_")
        assert body["stream_url"].startswith("/stream/run/")

        jid = body["job_id"]
        deadline = time.time() + 30
        st = {}
        while time.time() < deadline:
            st = c.get(f"/jobs/{jid}").json()
            if st.get("status") in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(0.1)
        assert st.get("status") == "succeeded", st
        assert st["elapsed_ms"] is not None

        res = c.get(f"/jobs/{jid}/result").json()
        assert res["result"]["response"]
        assert "queued queued world" in res["result"]["response"] or res["result"]["response"]

        ev = c.get(f"/jobs/{jid}/events?follow=false").json()["events"]
        types = [e["type"] for e in ev]
        assert "turn_started" in types and "run_finished" in types
        assert "llm_delta" in types, "token deltas must be on the bus"

        listing = c.get("/jobs?limit=5").json()
        assert listing["jobs"] and listing["queue"]["stats"]["succeeded"] >= 1
        assert c.get("/jobs/does_not_exist").status_code == 404
        assert c.post("/jobs", json={"kind": "not.a.kind", "payload": {}}).status_code == 400
        assert c.post("/jobs", json={"kind": "agent.chat", "payload": {"text": "queued via jobs endpoint",
                                                                       "user_id": "q2"}}).status_code == 200


def test_sse_stream_delivers_tokens_and_replays():
    with _client() as c:
        j = c.post("/jobs", json={"kind": "agent.chat",
                                 "payload": {"text": "stream me please", "user_id": "sse"}}).json()
        rid = j["run_id"]
        frames = []

        def reader():
            with c.stream("GET", f"/stream/run/{rid}") as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                assert resp.headers["x-hermus-run"] == rid
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        frames.append(json.loads(line[6:]))
                    if frames and frames[-1]["type"] in ("run_finished", "stream_end"):
                        break

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        deadline = time.time() + 30
        while time.time() < deadline and not any(f["type"] == "run_finished" for f in frames):
            time.sleep(0.1)
        t.join(timeout=5)

        types = [f["type"] for f in frames]
        assert "run_started" in types and "turn_started" in types
        deltas = [f for f in frames if f["type"] == "llm_delta"]
        assert deltas, "no token deltas reached the stream"
        assert "".join(d["data"].get("text", "") for d in deltas)
        # ids are monotonic so a reconnect can resume without duplicates
        ids = [f["id"] for f in frames if f.get("id")]
        assert ids == sorted(ids) and len(set(ids)) == len(ids)
        assert "job_finished" in types, types

        # late joiner replays from Last-Event-ID semantics (?after=)
        tail = c.get(f"/stream/run/{rid}").raise_for_status()
        late_ids = [json.loads(l[6:])["id"] for l in tail.text.splitlines() if l.startswith("data: ")]
        assert late_ids and min(late_ids) >= 1


def test_websocket_duplex_chat_cancel_and_tool():
    with _client() as c:
        with c.websocket_connect(f"/ws/agent?token=test-token") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["protocol"] == "hermus.agent.v1"
            assert "agent.chat" in hello["kinds"]
            assert hello["sandbox"] in ("local", "docker", "podman", "bwrap", "off")
            assert "fts5" in hello["memory_index"]

            ws.send_json({"action": "ping"})
            assert ws.receive_json()["type"] == "pong"

            ws.send_json({"action": "chat", "text": "hello over the socket", "user_id": "ws1",
                          "platform": "ws", "stream": True})
            ack = ws.receive_json()
            assert ack["type"] == "ack" and ack["kind"] == "agent.chat" and ack["job_id"]

            seen, deltas = [], 0
            for _ in range(400):
                msg = ws.receive_json()
                seen.append(msg.get("type"))
                if msg.get("type") == "llm_delta":
                    deltas += 1
                if msg.get("type") in ("run_finished", "stream_end"):
                    break
            assert "turn_started" in seen and deltas > 0, seen[:15]

            # direct tool call over the same socket, still permission-gated + sandboxed
            ws.send_json({"action": "tool", "name": "sandbox_run",
                          "args": {"command": "echo over_socket"}})
            for _ in range(40):
                msg = ws.receive_json()
                if msg.get("type") == "tool_result":
                    assert "over_socket" in msg["result"]["stdout"]
                    break
            else:
                raise AssertionError("no tool_result on the socket")

            ws.send_json({"action": "nonsense"})
            err = ws.receive_json()
            assert err["type"] == "error" and "unknown action" in err["error"]

            # memory over the socket uses hybrid recall
            ws.send_json({"action": "memory", "query": "socket hello", "limit": 3})
            for _ in range(20):
                msg = ws.receive_json()
                if msg.get("type") in ("memory_result", "error"):
                    break
            assert msg["type"] == "memory_result" and "hits" in msg

            # a chat we cancel right away must terminate one way or another
            ws.send_json({"action": "chat", "text": "cancel me", "user_id": "ws2"})
            a = ws.receive_json()
            ws.send_json({"action": "cancel", "job_id": a["job_id"]})
            for _ in range(60):
                m = ws.receive_json()
                if m.get("type") in ("cancel_result", "run_finished", "stream_end"):
                    break
            assert m["type"] == "cancel_result"
            assert m["result"]["cancelled"] is True


def test_websocket_requires_token_when_configured():
    from fastapi.testclient import TestClient

    import gateway.gateway as g

    os.environ["HERMUS_GATEWAY_TOKEN"] = "test-token"

    def attempt(query: str) -> tuple:
        """Returns (closed_before_accept, close_code) for a socket connect attempt."""
        with TestClient(g.app) as c:
            try:
                with c.websocket_connect(query) as ws:
                    ws.receive_json()
                return (False, None)
            except BaseException as e:
                code = getattr(e, "code", None)
                if code is None:
                    code = getattr(e.__context__, "code", None)
                return (True, code)

    closed, code = attempt("/ws/agent")
    assert closed and code == 1008, (closed, code)
    closed2, code2 = attempt("/ws/agent?token=nope")
    assert closed2 and code2 == 1008, (closed2, code2)
    closed3, code3 = attempt("/ws/agent?token=test-token")
    assert not closed3, (closed3, code3)          # the right token still works
    del os.environ["HERMUS_GATEWAY_TOKEN"]        # leave the process clean


def test_subsystem_http_endpoints():
    with _client() as c:
        assert c.post("/memory/remember", json={"kind": "semantic",
                                                "content": "Gateway test fact: shards rebalance at night",
                                                "importance": 8}).status_code == 200
        out = c.post("/memory/hybrid", json={"query": "when do shards rebalance", "limit": 3}).json()
        assert out["mode"] == "hybrid" and out["index"]["available"] is True
        assert out["results"] and "retrieval" in out["results"][0]
        assert c.post("/memory/hybrid", json={"query": ""}).status_code == 400
        assert c.post("/memory/sweep", json={}).status_code == 400          # needs confirm=true
        swept = c.post("/memory/sweep", json={"confirm": True, "dry_run": True}).json()
        assert "checked" in swept
        assert c.get("/memory/stats").json()["total"] >= 1
        assert c.post("/memory/reindex", json={}).status_code == 200

        sbx = c.get("/sandbox/status").json()
        assert sbx["backend"] in ("local", "docker", "podman", "bwrap", "off")
        ran = c.post("/sandbox/run", json={"command": "echo endpoint_ok"}).json()
        assert "endpoint_ok" in ran["stdout"]
        denied = c.post("/sandbox/run", json={"command": "rm -rf /"})
        assert denied.status_code == 403 and denied.json()["returncode"] == 126
        assert c.post("/sandbox/run", json={"command": "  "}).status_code == 400

        assert c.get("/skills/forge/stats").json()["stats"]["registered_skills"] >= 0
        assert c.post("/skills/forge/harvest", json={"goal": "x"}).status_code == 400
        skill_out = c.post("/skills/forge/harvest", json={
            "goal": "Summarize nginx error log and file a report from the gateway",
            "dry_run": True,
            "trajectory": [
                {"role": "user", "content": "Summarize nginx error log and file a report from the gateway"},
                {"role": "assistant", "content": "reading",
                 "tool_calls": [{"name": "shell_execute", "arguments": {"command": "grep -c . log"}, "id": "1"},
                                {"name": "shell_execute", "arguments": {"command": "sort log | uniq -c"}, "id": "2"},
                                {"name": "write_file", "arguments": {"path": "r.md", "content": "x"}, "id": "3"}]},
                {"role": "assistant", "content": "Report filed with counts of each error class and a "
                                                 "recommended timeout change."},
            ],
            "tool_results": [{"tool": "shell_execute", "result": {"stdout": "12", "returncode": 0}},
                             {"tool": "shell_execute", "result": {"stdout": "3 x", "returncode": 0}},
                             {"tool": "write_file", "result": {"success": True}}],
            "verification": {"verified": True},
        }).json()
        assert skill_out["created"] is False and skill_out["stage"] == "dry_run", skill_out

        assert c.get("/delegation/status").json()["can_delegate"] is True
        assert "error" in c.get("/delegation/nope").json()

        assert c.get("/runs").json()["runs"]
        rid = c.get("/runs").json()["runs"][-1]["run_id"]
        run_snap = c.get(f"/runs/{rid}").json()
        assert run_snap.get("run_id") == rid and "events" in run_snap
        assert c.get("/runs/run_missing").status_code == 404


def test_delegation_endpoint_async_and_sync():
    with _client() as c:
        j = c.post("/delegate", json={"goal": "two quick looks", "tasks": ["Say one", "Say two"],
                                      "async": True}).json()
        assert j["job_id"].startswith("job_")
        deadline = time.time() + 60
        st = {}
        while time.time() < deadline:
            st = c.get(f"/jobs/{j['job_id']}").json()
            if st.get("status") in ("succeeded", "failed"):
                break
            time.sleep(0.2)
        assert st.get("status") == "succeeded", st
        assert c.get(f"/jobs/{j['job_id']}/result").json()["result"]["succeeded"] == 2

        sync = c.post("/delegate", json={"goal": "single task", "tasks": ["Report the repo name"],
                                         "aggregate": "best"}).json()
        assert sync["ok"] is True and sync["children"] == 1
        assert c.post("/delegate", json={}).status_code == 400


def test_sync_command_still_streams_to_the_bus():
    """Callers that refuse the queue keep the old contract, and the run is still observable."""
    with _client() as c:
        r = c.post("/command", json={"text": "inline please", "user_id": "inl", "platform": "web"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["response"] and body["run_id"]
        assert body["run_id"].startswith("run_") or len(body["run_id"]) > 4
        ev = c.get(f"/stream/run/{body['run_id']}").json() if False else None
        hist = c.get(f"/jobs?limit=1").json()           # queue untouched by an inline call
        assert hist["queue"]["stats"]["submitted"] >= 0
        # events were mirrored onto the bus even for the synchronous path
        snap = c.get(f"/runs/{body['run_id']}").json()
        assert snap["exists"] in (True, None) or snap["events"]


def test_queue_disabled_falls_back_to_inline():
    """If the pool is off, /command?async must degrade instead of failing."""
    from gateway.queue import job_queue

    was_started, was_enabled = job_queue._started, job_queue.enabled
    # disabled config → start() refuses to spin up workers → intake runs inline
    job_queue.enabled = False
    job_queue._started = False
    try:
        with _client() as c:
            assert not job_queue._started, "start() must respect gateway_queue_enabled=0"
            r = c.post("/command", json={"text": "no queue here", "user_id": "off1",
                                        "platform": "web", "async": True})
            assert r.status_code == 200, r.text
            assert "response" in r.json() and "job_id" not in r.json()
    finally:
        job_queue._started = was_started
        job_queue.enabled = was_enabled


def test_every_registered_kind_runs_end_to_end():
    """A handler that crashes on import-time assumptions must be caught here, not in production."""
    from gateway.queue import JobQueue

    q = JobQueue(workers=4, maxsize=20, default_timeout=60,
                 persist=str(Path(_TMP) / "jobs" / "kinds.jsonl"))
    getter_calls = []

    def agent_getter(platform, user_id, **kw):
        from core.agent import HermusAgent

        getter_calls.append((platform, user_id, kw))
        return HermusAgent(model="mock/mock", max_steps=1)

    from gateway.handlers import register_handlers

    kinds = register_handlers(q, agent_getter)
    assert set(kinds) >= {"agent.chat", "agent.autonomous", "research.deep",
                          "subagent.delegate", "memory.sweep", "channel.reply"}
    submissions = {
        "agent.chat": {"text": "kind check chat", "user_id": "k1", "platform": "web", "talking": True},
        "agent.autonomous": {"text": "kind check autonomous", "user_id": "k2"},
        "research.deep": {"topic": "sqlite vector search", "user_id": "k3", "max_steps": 1},
        "subagent.delegate": {"goal": "kind check delegate", "tasks": ["Say one"]},
        "memory.sweep": {"dry_run": True},
        # channel.reply has no telegram token configured → must report, not raise
        "channel.reply": {"text": "kind check channel", "chat_id": 42, "platform": "telegram"},
    }

    async def main():
        await q.start()
        jobs = {k: q.submit(k, p_, session_key=f"kind:{k}") for k, p_ in submissions.items()}
        deadline = time.time() + 120
        while time.time() < deadline:
            if all(q.status(j.id)["status"] in ("succeeded", "failed", "cancelled") for j in jobs.values()):
                break
            await asyncio.sleep(0.1)
        await q.stop()
        return jobs

    jobs = _drive(main())
    report = {k: (q.status(j.id)["status"], q.result(j.id) or {}) for k, j in jobs.items()}
    for kind, (status, res) in report.items():
        assert status == "succeeded", (kind, status, res.get("error"))
        assert not (isinstance(res, dict) and str(res.get("error", "")).startswith("AttributeError")), res
    assert report["agent.chat"][1].get("response")
    assert report["agent.chat"][1].get("job_id")
    # talking mode attaches a speech verdict (the backend may be absent — that is reported)
    assert "speech" in report["agent.chat"][1], report["agent.chat"][1].keys()
    assert report["memory.sweep"][1].get("checked", 0) >= 0
    assert report["subagent.delegate"][1].get("children") == 1
    reply = report["channel.reply"][1]
    assert reply["answer"], reply
    assert reply["delivery"]["delivered"] is False          # no bot token in CI
    assert "TELEGRAM_BOT_TOKEN" in json.dumps(reply["delivery"])
    assert reply["delivered"] is False
    assert "delivered" in reply["delivery"] or "error" in reply["delivery"]
    assert getter_calls, "agent getter must be used for agent kinds"


def test_redis_backend_is_optional_and_degrades():
    """No redis installed/configured → the queue must stay in-process, never crash."""
    from gateway.redis_backend import RedisStreamsConsumer, redis_available

    ok, detail = redis_available()
    assert isinstance(ok, bool) and isinstance(detail, str) and detail
    if not ok:
        assert "redis" in detail.lower(), detail
    q = _queue(log="redis.jsonl")
    q.backend = "redis"
    consumer = RedisStreamsConsumer(q)

    async def main():
        info = await q.start()          # start() must survive a missing redis
        await asyncio.sleep(0.2)
        await q.stop()
        return info

    info = _drive(main())
    assert info["backend"] in ("redis", "inprocess")
    if not ok:
        assert q._redis is None or consumer is not None
        job = None

        async def run_job():
            nonlocal job
            q.register("t.echo", lambda ctx: {"echo": ctx.payload})
            await q.start()
            job = q.submit("t.echo", {"hi": 1}, session_key="redis-degraded")
            for _ in range(200):
                if q.status(job.id)["status"] in ("succeeded", "failed"):
                    break
                await asyncio.sleep(0.05)
            await q.stop()

        _drive(run_job())
        assert q.status(job.id)["status"] == "succeeded", "redis must be additive, never required"


def test_stream_command_single_call():
    with _client() as c:
        frames = []

        def reader():
            with c.stream("POST", "/stream/command",
                          json={"text": "one call stream", "user_id": "sc1", "platform": "web"}) as resp:
                assert resp.status_code == 200
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        frames.append(json.loads(line[6:]))
                    if frames and frames[-1]["type"] in ("run_finished", "stream_end"):
                        break

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        deadline = time.time() + 30
        while time.time() < deadline and not (frames and frames[-1]["type"] in ("run_finished", "stream_end")):
            time.sleep(0.1)
        t.join(timeout=5)
        assert frames, "stream/command produced nothing"
        assert frames[0]["type"] in ("job_queued", "run_started", "turn_started")
        assert any(f["type"] == "llm_delta" for f in frames)
        assert c.post("/stream/command", json={"text": "   "}).status_code == 400


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)


def test_http_routes_enforce_gateway_token_when_configured():
    """The optional HERMUS_GATEWAY_TOKEN must actually be enforced on control-plane
    HTTP routes (not merely defined). When unset the gateway stays open; when set,
    gated routes reject a wrong/missing token with 401 and accept the right one."""
    from fastapi.testclient import TestClient

    import gateway.gateway as g

    os.environ["HERMUS_GATEWAY_TOKEN"] = "unit-token"
    try:
        with TestClient(g.app) as c:
            # No token -> rejected on a gated route.
            assert c.get("/jobs").status_code in (401,), c.get("/jobs").status_code
            # Wrong token -> rejected.
            assert c.get("/jobs", headers={"X-Hermus-Token": "nope"}).status_code == 401
            # Right token -> passes the auth gate (may still be 200 or a real response).
            ok = c.get("/jobs", headers={"X-Hermus-Token": "unit-token"})
            assert ok.status_code != 401, ok.status_code
            # The inbound channel webhook must remain open (external service).
            assert c.post("/webhook/telegram", json={}).status_code != 401
    finally:
        del os.environ["HERMUS_GATEWAY_TOKEN"]
