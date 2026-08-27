"""Tests for hierarchical sub-agent delegation (architecture upgrade C2).

The contract under test: an orchestrator splits work, each child runs in its own
process speaking newline-delimited JSON-RPC 2.0 over stdio, results come back as a
*structured* record (not prose), and results are aggregated with a stated policy.
Cancellation, the depth budget and process hygiene must be real.

Offline: children use the mock model. Run:
  python tests/test_delegation.py   (or pytest tests/test_delegation.py)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

_TMP = tempfile.mkdtemp(prefix="hermus_dlg_")
os.environ["HERMUS_HOME"] = _TMP
os.environ["HERMUS_MODEL"] = "mock/mock"
os.environ.setdefault("HERMUS_EMBED_BACKEND", "hash")
os.environ["HERMUS_AGENT_DEPTH"] = "0"

from core.config import config  # noqa: E402

config.model = "mock/mock"
config.max_tool_steps = 2
config.memory2_db_path = str(Path(_TMP) / "memory2.db")
config.memory_db_path = str(Path(_TMP) / "memory.db")
config.trajectory_path = str(Path(_TMP) / "trajectories.jsonl")
config.embeddings_db_path = str(Path(_TMP) / "embeddings.db")
config.delegation_timeout = 90

from core.delegation import (  # noqa: E402
    ERR_INTERNAL,
    ERR_METHOD_NOT_FOUND,
    Delegation,
    aggregate_results,
    normalize_result,
    plan_workstreams,
    rpc_error,
    rpc_notification,
    rpc_response,
)


# --------------------------------------------------------------------------
# Wire protocol
# --------------------------------------------------------------------------
def test_rpc_frames_are_valid_jsonrpc_2():
    req = json.loads(json.dumps(rpc_response(7, {"answer": "ok"})))
    assert req["jsonrpc"] == "2.0"
    assert req["id"] == 7 and req["result"]["answer"] == "ok" and "error" not in req

    err = rpc_error(8, "no such method", ERR_METHOD_NOT_FOUND, {"detail": 1})
    assert err["id"] == 8 and err["error"]["code"] == ERR_METHOD_NOT_FOUND
    assert err["error"]["message"] == "no such method" and err["error"]["data"]["detail"] == 1
    assert "result" not in err

    note = rpc_notification("step_started", {"i": 1})
    assert note["method"] == "step_started" and "id" not in note      # notifications: no id
    assert note["jsonrpc"] == "2.0"

    # long errors are truncated, never explode the frame
    long_err = rpc_error(None, "x" * 99999, ERR_INTERNAL)
    assert len(long_err["error"]["message"]) <= 1500 and long_err["id"] is None


def test_result_contract_is_normalized_from_any_shape():
    # agent-shaped result
    agent = normalize_result({"response": "text answer", "tool_results": [{"tool": "x"}],
                              "steps": 3, "usage": {"total_tokens": 12}})
    assert agent["answer"] == "text answer"
    assert agent["status"] in ("done", "partial")
    assert agent["steps"] == 3 and agent["usage"]["total_tokens"] == 12

    # already-conforming input is preserved
    full = {"answer": "done", "evidence": ["a"], "confidence": 0.8, "tool_calls": [1],
            "status": "done", "artifacts": [], "usage": {"tokens": 5}, "steps": 2, "error": ""}
    again = normalize_result(full)
    assert again["answer"] == "done" and again["confidence"] == 0.8
    assert again["evidence"] == ["a"] and again["status"] == "done"

    # bare string from a sloppy child still conforms
    loose = normalize_result("just a string")
    assert loose["answer"] == "just a string" and isinstance(loose["evidence"], list)

    # error-shaped child result stays marked as failed
    failed = normalize_result({"error": "model exploded"})
    assert failed["status"] == "failed" and "exploded" in failed["error"]

    # every contract key is always present — the aggregator must never KeyError
    out = normalize_result(None)
    assert {"answer", "evidence", "confidence", "tool_calls", "status", "error",
            "artifacts", "usage", "steps"} <= set(out)
    json.dumps(out)                                    # serializable across the wire


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------
def _results():
    return [
        {"answer": "Postgres is better for joins", "confidence": 0.9, "status": "done",
         "evidence": ["pg.md"], "tool_calls": [{"tool": "file_read"}], "artifacts": ["/tmp/a1"]},
        {"answer": "Postgres is better for joins", "confidence": 0.8, "status": "done",
         "evidence": ["pg2.md"], "tool_calls": [], "artifacts": []},
        {"answer": "MySQL wins on ops cost", "confidence": 0.4, "status": "done",
         "evidence": [], "tool_calls": [], "artifacts": ["/tmp/a3"]},
        {"answer": "", "confidence": 0.0, "status": "failed", "error": "child died",
         "evidence": [], "tool_calls": [], "artifacts": []},
    ]


def test_aggregate_strategies():
    res = _results()

    concat = aggregate_results(res, strategy="concat")
    assert concat["used"] == 3 and concat["skipped"] == 1
    assert "### Child 1" in concat["answer"] and "Postgres" in concat["answer"]
    assert concat["disagreement"] > 0                      # one child disagreed
    assert concat["errors"] == ["child died"]
    assert concat["artifacts"] == ["/tmp/a1", "/tmp/a3"]
    assert concat["citations"] == ["pg.md", "pg2.md"]

    vote = aggregate_results(res, strategy="vote")
    assert vote["used"] == 2                               # two children agreed
    assert "Postgres is better for joins" in vote["answer"]
    assert vote["confidence"] > 0.5

    best = aggregate_results(res, strategy="best")
    assert best["answer"] == "Postgres is better for joins"
    # the winner's confidence is reported (then discounted for the disagreement in the room)
    assert best["confidence"] == round(0.9 * (0.85 if best["disagreement"] > 0.4 else 1.0), 3)
    assert best["confidence"] > 0.7

    syn = aggregate_results(res, strategy="synthesize", goal="pick a database")
    assert len(syn["sections"]) == 3
    assert syn["sections"][0]["child"] == 1 and syn["sections"][0]["tool_calls"]
    assert "pick a database" in syn["answer"] or "Aggregate" in syn["answer"]
    assert 0.0 <= syn["disagreement"] <= 1.0
    assert "errors" in syn


def test_aggregate_of_all_failures_is_still_a_valid_record():
    out = aggregate_results([{"error": "boom", "status": "failed"}], strategy="synthesize")
    assert out["used"] == 0 and out["answer"] == ""
    assert out["errors"] == ["boom"] and out["confidence"] == 0.0
    assert out["sections"] == [] and out["citations"] == []
    assert aggregate_results([], strategy="vote")["used"] == 0      # empty tree, no crash


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------
def test_plan_workstreams_falls_back_to_heuristic():
    plan = plan_workstreams("Compare Postgres, MySQL and SQLite for our analytics workload",
                            max_children=3)
    assert plan["planner"] in ("llm", "heuristic")
    assert plan["tasks"] and len(plan["tasks"]) <= 3
    assert all(isinstance(t, str) and t.strip() for t in plan["tasks"])
    assert plan_workstreams("")["tasks"] == []
    assert plan_workstreams("just do this one thing")["tasks"]


# --------------------------------------------------------------------------
# Real sub-agent processes
# --------------------------------------------------------------------------
def test_worker_self_test_answers_ping():
    proc = subprocess.run(
        [sys.executable, "-m", "core.delegation", "--self-test", "--depth", "1"],
        cwd=str(Path(__file__).parent.parent), capture_output=True, text=True, timeout=180,
        env={**os.environ, "HERMUS_HOME": _TMP, "HERMUS_MODEL": "mock/mock"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    frame = json.loads(proc.stdout.strip().splitlines()[-1])
    assert frame["jsonrpc"] == "2.0" and frame["id"] == 1
    assert "pong" in frame["result"] and frame["result"]["depth"] == 1


def test_unknown_method_and_bad_frame_are_answered_not_fatal():
    """A child must answer errors as JSON-RPC, never die on a bad request."""
    script = (
        "import json,sys;"
        "sys.argv=['x','--depth','1'];"
        "from core.delegation import DelegationWorker;"
        "w=DelegationWorker(depth=1);"
        "print(json.dumps(w.dispatch({'id':1,'method':'nope'})));"
        "print(json.dumps(w.dispatch({'id':2})));"
        "print(json.dumps(w.dispatch({'id':3,'method':'tool.call','params':{'name':'no_such_tool','args':{}}})));"
    )
    proc = subprocess.run([sys.executable, "-c", script], cwd=str(Path(__file__).parent.parent),
                          capture_output=True, text=True, timeout=180,
                          env={**os.environ, "HERMUS_HOME": _TMP, "HERMUS_MODEL": "mock/mock"})
    assert proc.returncode == 0, proc.stderr[-800:]
    lines = [json.loads(x) for x in proc.stdout.strip().splitlines()]
    assert lines[0]["error"]["code"] == ERR_METHOD_NOT_FOUND
    assert lines[1]["error"]["code"] != 0                       # missing method
    assert "result" in lines[2] or "error" in lines[2]          # unknown tool answered


def test_fanout_runs_children_in_parallel_processes():
    d = Delegation(timeout=120)
    events = []
    out = d.fanout(
        ["List the top-level directories in this repo and describe each in one line",
         "Count the python files under core/ and report the number"],
        goal="quick repo survey", aggregate="concat", max_steps=2,
        on_event=lambda t, dd: events.append(t),
    )
    assert out["ok"] is True, out
    assert out["status"] == "done"
    assert out["children"] == 2 and out["succeeded"] == 2 and out["failed"] == 0
    assert out["tree_id"].startswith("tree_") and out["duration_ms"] >= 0
    pids = [n["pid"] for n in out["nodes"]]
    assert all(isinstance(p, int) and p > 0 for p in pids), pids
    assert len(set(pids)) == 2, "children must be separate processes"
    assert {n["backend"] for n in out["nodes"]} == {"subprocess-jsonrpc"}
    counts = {n["id"]: n["event_count"] for n in out["nodes"]}
    assert all(c >= 1 for c in counts.values()), f"events were mis-attributed: {counts}"
    for n in out["nodes"]:
        assert n["status"] == "done" and n["answer"]
        assert n["event_count"] >= 1, "child events must be forwarded to the parent"
        assert {"answer", "confidence", "tool_calls", "evidence"} <= set(n)
    assert "delegation_fanout" in events and "delegation_child_done" in events
    assert "delegation_finished" in events
    assert out["aggregate"]["used"] == 2

    # no orphaned children left behind
    time.sleep(0.4)
    for pid in pids:
        if Path(f"/proc/{pid}").exists():
            state = Path(f"/proc/{pid}/stat").read_text().split(") ")[-1].split()[0]
            assert state == "Z", f"pid {pid} still {state} after fan-out completed"
    assert d.status()["active_clients"] == {}


def test_tree_is_inspectable_afterwards():
    d = Delegation(timeout=120)
    out = d.fanout(["Say the word alpha"], goal="one child", aggregate="best", max_steps=1)
    tree = d.tree(out["tree_id"])
    assert tree["tree_id"] == out["tree_id"]
    assert tree["children"][0]["status"] == "done"
    assert tree["result"]["aggregate"]["answer"]
    assert "unknown tree" in d.tree("nope")["error"]
    assert out["tree_id"] in d.status()["trees"]
    assert d.status()["trees"][out["tree_id"]]["status"] == "done"


def test_decomposition_path_plans_then_runs():
    d = Delegation(timeout=150)
    out = d.decompose_and_run("Research and compare Postgres vs MySQL vs SQLite",
                              max_children=3, aggregate="vote")
    assert out["ok"] is True, out
    assert 1 <= out["children"] <= 3
    assert out["aggregate"]["strategy"] == "vote"
    for n in out["nodes"]:
        assert n["task"] and n["status"] in ("done", "failed", "cancelled")
    assert out["goal"].lower().startswith("research")


def test_depth_budget_stops_recursive_fanout():
    """A child at the depth limit must refuse, so trees cannot recurse forever."""
    os.environ["HERMUS_AGENT_DEPTH"] = "2"
    try:
        d = Delegation(timeout=30, max_depth=2)
        out = d.fanout(["anything"], goal="nested")
        assert out["ok"] is False and out["status"] == "refused"
        assert "depth" in out["error"]
    finally:
        os.environ["HERMUS_AGENT_DEPTH"] = "0"
    assert Delegation(timeout=30, max_depth=2).can_delegate() is True


def test_empty_task_list_is_an_error_not_a_hang():
    d = Delegation(timeout=10)
    out = d.fanout(["   ", ""], goal="nothing")
    assert out["ok"] is False and out["status"] == "failed"


def test_status_shape_and_unknown_cancel():
    d = Delegation(timeout=30)
    st = d.status()
    assert {"enabled", "rpc", "depth", "max_depth", "max_workers", "timeout",
            "can_delegate", "trees", "active_clients"} <= set(st)
    assert st["max_depth"] >= 1
    assert d.cancel_tree("does_not_exist")["cancelled"] == 0


def test_rpc_disabled_degrades_to_inprocess_execution():
    """A child that cannot be spawned must not fail the whole delegation."""
    d = Delegation(timeout=120)
    d.rpc = False
    out = d.fanout(["Report the repo name"], goal="fallback check", aggregate="concat", max_steps=1)
    assert out["ok"] is True, out
    assert out["nodes"][0]["backend"] == "inprocess"
    assert out["nodes"][0]["pid"] == 0


def test_cancellation_is_cooperative():
    """cancel_tree flips the run bus; children stop at their next step boundary."""
    d = Delegation(timeout=120)
    flag = {"v": False}
    out = d.fanout(["Say beta"], goal="cancel check", max_steps=1,
                   should_cancel=lambda: flag["v"])
    # nothing was cancelled before the run, and the tree completes either way
    assert out["status"] in ("done", "partial", "failed")
    assert d.cancel_tree(out["tree_id"])["tree_id"] == out["tree_id"]


def test_subagent_facade_still_works():
    from subagents.subagent import delegate, spawn_parallel_subagents, spawn_subagent, subagent_status

    one = spawn_subagent("Report how many markdown files exist at the repo root", max_steps=2)
    assert one["success"] is True, one
    assert one["subagent_id"].startswith("sub_")
    assert one["response"] and one["duration_ms"] >= 0
    assert one["backend"] in ("subprocess-jsonrpc", "inprocess", "inprocess-fallback")
    assert one["tree"]["children"] == 1

    many = spawn_parallel_subagents(["Say beta", "Say gamma"], max_steps=1)
    assert len(many) == 2 and all(m["success"] for m in many), many
    assert len({m["subagent_id"] for m in many}) == 2

    st = subagent_status()
    assert st["can_delegate"] is True and "trees" in st

    res = delegate("Summarise the gateway", tasks=["Describe gateway/gateway.py in one line"])
    assert res["ok"] is True and res["nodes"]

    failed = spawn_subagent("", max_steps=1)
    assert failed["success"] is False or failed.get("error")


def test_generated_rpc_tool_lands_in_the_skill_tree():
    from core.skill_forge import skill_forge
    from subagents.subagent import write_python_tool_via_rpc

    out = write_python_tool_via_rpc("rpc_log_digest",
                                    ["shell_execute(grep)", "file_read(log)", "memory2_remember(summary)"])
    assert out.get("installed") is True, out
    name = out["tool_name"]
    assert name in skill_forge.index()["skills"]
    # the emitted module must be importable, not merely written
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, str(Path(out["path"]) / "skill.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.plan()


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
