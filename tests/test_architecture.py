"""Tests for the architecture upgrades (Memory 2.0, router, autonomous loop,
background agents, permissions, research, computer control, watchdog, profiles).

Offline + dependency-light: no models or network required. A temporary
HERMUS_HOME is set BEFORE importing modules that bind a global workspace.

Run:  python tests/test_architecture.py   (or pytest tests/test_architecture.py)
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

# Isolate the workspace before importing anything that binds a global instance.
_TMP = tempfile.mkdtemp(prefix="hermus_test_")
os.environ["HERMUS_HOME"] = _TMP

from core.config import config  # noqa: E402

config.model = "mock/mock"
# Isolate memory stores so tests don't pollute the shared repo data/*.db
config.memory_db_path = str(Path(_TMP) / "memory.db")
config.memory2_db_path = str(Path(_TMP) / "memory2.db")
config.trajectory_path = str(Path(_TMP) / "trajectories.jsonl")
config.user_model_path = str(Path(_TMP) / "user_model.json")
config.embeddings_db_path = str(Path(_TMP) / "embeddings.db")


# --------------------------------------------------------------------------
# Workspace
# --------------------------------------------------------------------------
def test_workspace_layout_and_projects():
    from core.workspace import Workspace, dump_yaml, load_yaml

    ws = Workspace(base_dir=_TMP)
    assert all(p.exists() for p in ws.dirs.values())

    r = ws.create_project("my project!", description="a test")
    assert r["success"], r
    assert ws.get_project("my project!") is not None
    assert "my_project" in r["path"]  # sanitized (space→_, trailing junk stripped)

    names = [p["name"] for p in ws.list_projects()]
    assert "my project!" in names  # list_projects returns the display name

    assert ws.set_current_project("my project!")["success"]
    assert ws.current_project() == "my project!"

    assert ws.delete_project("my project!")["success"]

    # flat yaml round-trip
    y = dump_yaml({"name": "x", "num": 3, "flag": True, "tags": ["a", "b c"]})
    back = load_yaml(y)
    assert back["name"] == "x" and back["num"] == 3 and back["flag"] is True
    assert back["tags"] == ["a", "b c"]


# --------------------------------------------------------------------------
# Memory 2.0
# --------------------------------------------------------------------------
def test_memory2_typed_and_scored_recall():
    from core.memory2 import Memory2

    m = Memory2(db_path=str(Path(_TMP) / "mem2.db"))
    m.remember("semantic", "Hermus is a free local AI agent", importance=9)
    m.remember("episodic", "We fixed a JSON parse bug in the skill engine", success=True)
    m.remember("procedural", "To deploy: run tests then git push", success=True)
    m.remember("procedural", "To deploy: run tests then git push", success=True)  # frequency bump
    m.remember("project", "The dashboard uses FastAPI + vanilla JS", project="web")

    # recall scores relevant memories higher
    res = m.recall("How do I deploy?")
    assert res, "recall returned nothing"
    assert res[0]["kind"] == "procedural"
    assert res[0]["score"] > 0
    assert "score" in res[0] and "signals" in res[0]

    # project scoping
    proj = m.recall("dashboard", project="web")
    assert any(p["kind"] == "project" for p in proj)

    # frequency merge: only one procedural deploy memory
    kinds = m.store.all(kind="procedural")
    deploy = [k for k in kinds if "deploy" in k["content"]]
    assert len(deploy) == 1

    # forget works
    mid = deploy[0]["id"]
    assert m.store.forget(mid)


# --------------------------------------------------------------------------
# Model Router 2.0
# --------------------------------------------------------------------------
def test_router_classify_and_select_falls_back():
    from core.router2 import ModelRouter

    r = ModelRouter()
    assert r.classify_task("fix the bug in this python function") == "code"
    assert r.classify_task("explain why the tradeoff matters") == "reasoning"
    assert r.classify_task("what is in this screenshot?") == "vision"
    assert r.classify_task("hello") == "chat"
    assert 1 <= r.classify_difficulty("hello") <= 5
    assert r.estimate_context_tokens("a" * 400) >= 100

    # no workers configured → graceful fallback, never crashes
    sel = r.select("rewrite this function to be faster")
    assert sel["task_type"] == "code"
    assert sel["provider"] in ("ollama", "mock")
    assert "model" in sel


# --------------------------------------------------------------------------
# Autonomous loop
# --------------------------------------------------------------------------
def test_marker_verifier_and_failure_classification():
    from core.verifiers import MarkerDiagnoser, MarkerVerifier

    # A clean result verifies; an error-marker result does not.
    assert MarkerVerifier().verify("task", "SUCCESS: completed work")["ok"] is True
    bad = MarkerVerifier().verify("task", "error: something failed")
    assert bad["ok"] is False
    assert "error" in bad["problems"]

    # The diagnoser turns the failing marker into a retry hint (repair semantic).
    hint = MarkerDiagnoser().diagnose("task", None, bad)
    assert hint["retry"] is True and "Previous attempt failed" in hint["hint"]

    # Repair semantics: once the flaky executor returns a clean result it verifies.
    assert MarkerVerifier().verify("make it work", "SUCCESS: completed make it work")["ok"] is True


# --------------------------------------------------------------------------
# Persistent background agents
# --------------------------------------------------------------------------
def _new_agents_dir(tmp_path):
    from core.workspace import workspace

    ag = tmp_path / "agents"
    workspace.base_dir = tmp_path
    ag.mkdir(parents=True, exist_ok=True)
    return ag


def test_agent_manager_lifecycle(tmp_path):
    """The fleet facade is the registry surface; the canonical Job queue owns execution."""
    from core.fleet.bus import FleetBus
    from core.fleet.facade import AgentManagerFacade
    from core.fleet.registry import FleetRegistry

    def stub_chat_fn(messages):
        task = ""
        for turn in messages:
            if turn.get("role") == "user":
                task = turn.get("content") or ""
        return {"content": f"stub-reply:{task}", "tokens": 7}

    bus = FleetBus(base_dir=tmp_path / "fleet")
    bus.load()
    registry = FleetRegistry(bus, chat_fn=stub_chat_fn, roster_dir=tmp_path / "agents")
    am = AgentManagerFacade(registry=registry)

    assert am.create("tester", role="generic")["success"]
    assert not am.create("tester", role="generic")["success"]  # duplicate

    # stop pauses the live roster agent; start on a non-paused agent is refused
    # honestly (there is no process to start in the fleet model).
    stopped = am.stop("tester")
    assert stopped["success"] and stopped["registry"] == "fleet-registry"
    status = am.status("tester")
    assert status["success"] and status["status"] == "PAUSED"
    assert status["alive"] is True
    started = am.start("tester")
    assert started["success"] and started["registry"] == "fleet-registry"
    # The fleet model owns no bespoke per-agent state/jobs/results files.
    ag = tmp_path / "agents" / "tester"
    assert not (ag / "state.json").exists()

    # Job submission routes to FleetRegistry.assign (synchronous, idempotent).
    job = am.submit_job("tester", {"task": "hello"})
    assert job.get("success") is True
    assert job.get("executed") is True
    assert job.get("result") == "stub-reply:hello"
    assert (ag / "jobs").exists() is False

    # The watchdog reports the roster honestly — no fabricated restarts.
    tick = am.watchdog_tick(restart=False)
    assert tick["recovery_owner"] == "fleet-registry"
    assert isinstance(tick["stale"], list)


def test_no_production_path_creates_agent_json_jobs():
    """Architecture gate: no module owns a bespoke jobs/*.json/results/*.json lifecycle.

    Agent job execution is owned by the canonical Job system; agent identity is
    owned by the Fleet Registry. The retired ``core/agent_manager.py`` must not
    come back, and no other module may define its own per-agent JSON job queue.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    # The legacy split-brain registry stays deleted.
    assert not (root / "core" / "agent_manager.py").exists(), "agent_manager.py must stay deleted"
    # No production module spawns its own background worker subprocess protocol.
    for p in list((root / "core").rglob("*.py")) + list((root / "gateway").rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        assert "worker_loop" not in text, f"{p} references removed worker_loop"
    # The only public agent-role Job kinds reach the canonical queue via the gateway.
    hp = (root / "gateway" / "handlers.py").read_text(encoding="utf-8")
    assert "agent.general" in hp and "agent.computer" in hp


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------
def test_permission_manager_decisions():
    from core.permissions import PermissionManager

    pm = PermissionManager()
    assert pm.check("read_file")["decision"] == "allow"
    assert pm.check("web_search")["decision"] == "allow"
    assert pm.check("delete_file")["decision"] == "ask"
    assert pm.check("shell_execute")["decision"] == "ask"
    assert pm.check("click")["decision"] == "deny"
    assert pm.check("credential_access")["decision"] == "deny"
    # risk escalation: shell with sudo → admin
    assert pm.check("shell_execute", args={"cmd": "sudo rm -rf /"})["risk"] == "admin"
    # per-agent override
    pm.set_policy("shell_execute", "allow", agent="bob")
    assert pm.check("shell_execute", agent="bob")["decision"] == "allow"
    assert pm.check("shell_execute", agent="alice")["decision"] == "ask"
    # audit trail
    assert len(pm.recent()) >= 1


# --------------------------------------------------------------------------
# Research pipeline
# --------------------------------------------------------------------------
def test_research_pipeline_offline():
    from core.research import ResearchPipeline

    fake = [
        {
            "title": "Python is fast",
            "url": "https://a.com/x",
            "snippet": "Python is fast for prototyping and widely used. It has strong libraries.",
        },
        {"title": "Python is fast", "url": "https://a.com/x", "snippet": "duplicate url"},
        {
            "title": "Python is slow",
            "url": "https://b.com/y",
            "snippet": "Python is not fast when compared to compiled languages like C.",
        },
        {
            "title": "Python ecosystem",
            "url": "https://c.com/z",
            "snippet": "Python has a huge ecosystem of libraries and frameworks.",
        },
    ]
    p = ResearchPipeline(search_fn=lambda q, limit: fake)
    out = p.run("Is Python fast?")
    assert out["answer"]
    # dedupe removed the duplicate URL
    urls = [s["url"] for s in out["sources"]]
    assert urls.count("https://a.com/x") == 1
    # contradiction detected between a.com (fast) and b.com (not fast)
    assert out["contradictions"], "expected a fast/not-fast contradiction"
    assert 0.0 <= out["confidence"] <= 1.0


# --------------------------------------------------------------------------
# Computer control (recorder / sampler / verifier)
# --------------------------------------------------------------------------
def test_computer_recorder_sampler_verifier():
    from PIL import Image

    from core.computer import FrameSampler, ScreenVerifier
    from core.computer.recorder import CallableSource, ScreenRecorder

    def frame(color):
        img = Image.new("RGB", (32, 32), color)
        return img

    seq = [frame("black"), frame("black"), frame("white"), frame("white"), frame("red")]
    sampler = FrameSampler(threshold=0.02)
    events = sampler.detect_changes([{"ts": str(i), "image": im} for i, im in enumerate(seq)])
    assert len(events) == 2  # black->white and white->red
    summary = sampler.summarize([{"ts": str(i), "image": im} for i, im in enumerate(seq)])
    assert summary["events"] == 2

    verifier = ScreenVerifier()
    assert verifier.screen_changed(frame("black"), frame("white"))["changed"] is True
    assert verifier.screen_changed(frame("black"), frame("black"))["changed"] is False

    # recorder with a callable source: start/stop/recent
    colors = iter(["black", "white", "red", "blue"])
    rec = ScreenRecorder(source=CallableSource(lambda: frame(next(colors, "blue"))), max_seconds=5, fps=50)
    assert rec.start()["success"]
    import time

    time.sleep(0.2)
    st = rec.stop()
    assert st["success"] and rec.recent(seconds=1) or rec.all_frames()
    assert rec.status()["running"] is False


# --------------------------------------------------------------------------
# Watchdog (self-healing)
# --------------------------------------------------------------------------
def test_watchdog_known_fix_and_rollback():
    from core.watchdog import Watchdog

    wd = Watchdog()
    r = wd.handle("JSONDecodeError: expecting value line 3 column 5")
    assert r["known"] and r["action"] == "apply_fix"
    assert r["fix"] == "json-parse"

    r2 = wd.handle("something completely novel")
    assert r2["known"] is False and r2["action"] == "diagnose"

    # tester/rollback integration
    wd2 = Watchdog()
    wd2.register_fix(r"boom", lambda e: {"ok": True}, "boom-fix")
    wd2.tester = lambda: False
    rolled = []
    wd2.rollbacker = lambda: rolled.append(1)
    r3 = wd2.handle("boom happened")
    assert r3["rolled_back"] is True and rolled == [1]
    assert r3["ok"] is False


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------
def test_profile_manager_isolated_memory():
    from core.profiles import ProfileManager

    pm = ProfileManager(profiles_dir=Path(_TMP) / "profiles")
    assert pm.create("coder")["success"]
    assert "senior software engineer" in pm.system_prompt("coder").lower()
    assert pm.create("coder")["success"] is False  # duplicate

    pm.remember("coder", "semantic", "prefers pytest over unittest")
    hits = pm.recall("coder", "testing framework")
    assert hits and hits[0]["kind"] == "semantic"

    # independent memory: a different profile doesn't see it
    pm.create("researcher")
    assert pm.recall("researcher", "testing framework") == []

    assert any(p["name"] == "coder" for p in pm.list())
    assert pm.delete("coder")["success"]


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
