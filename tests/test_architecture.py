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
    import time
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
def test_autonomous_loop_verify_and_repair():
    from core.autonomous import AutonomousRunner, Verifier

    # a flaky executor that fails once then succeeds
    calls = {"n": 0}

    def executor(step):
        calls["n"] += 1
        if calls["n"] == 1:
            return "error: something failed"
        return "SUCCESS: completed " + step

    runner = AutonomousRunner(executor=executor, verifier=Verifier(), max_repairs=3)
    report = runner.run("make it work")
    assert report.verified, report.to_dict()
    assert report.repairs >= 1
    assert "understand" in report.phases and "finish" in report.phases
    assert report.status == "done"

    # an always-failing executor exhausts repairs and does NOT verify
    runner2 = AutonomousRunner(executor=lambda s: "error: nope", verifier=Verifier(), max_repairs=1)
    rep2 = runner2.run("impossible")
    assert not rep2.verified and rep2.status == "failed"

    # custom planner splits into steps
    runner3 = AutonomousRunner(executor=lambda s: "done " + s,
                               verifier=Verifier(),
                               planner=lambda t: [t + " A", t + " B"])
    rep3 = runner3.run("task")
    assert len(rep3.steps) == 2 and rep3.verified


# --------------------------------------------------------------------------
# Persistent background agents
# --------------------------------------------------------------------------
def test_agent_manager_lifecycle():
    import time
    from core.agent_manager import AgentManager, worker_loop

    am = AgentManager()
    assert am.create("tester", role="generic")["success"]
    assert not am.create("tester", role="generic")["success"]  # duplicate

    st = am.start("tester")
    assert st["success"] and st["pid"]
    # wait for worker to write a running heartbeat
    for _ in range(50):
        if am.status("tester").get("status") == "running":
            break
        time.sleep(0.05)
    assert am.status("tester")["alive"] is True

    am.stop("tester")
    assert am.status("tester")["status"] == "stopped"

    # job queue drains via worker_loop run inline
    from core.workspace import workspace
    adir = workspace.dirs["agents"] / "tester" / "jobs"
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "0001.json").write_text('{"task": "hello"}')
    worker_loop("tester", handler=lambda j: {"ok": True, "got": j["task"]}, max_idle=0.05)
    assert am.status("tester")["jobs_done"] >= 1

    # watchdog detects nothing stale after clean stop (status != created/running)
    tick = am.watchdog_tick(restart=False)
    assert isinstance(tick["stale"], list)


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
        {"title": "Python is fast", "url": "https://a.com/x", "snippet": "Python is fast for prototyping and widely used. It has strong libraries."},
        {"title": "Python is fast", "url": "https://a.com/x", "snippet": "duplicate url"},
        {"title": "Python is slow", "url": "https://b.com/y", "snippet": "Python is not fast when compared to compiled languages like C."},
        {"title": "Python ecosystem", "url": "https://c.com/z", "snippet": "Python has a huge ecosystem of libraries and frameworks."},
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
    rec = ScreenRecorder(source=CallableSource(lambda: frame(next(colors, "blue"))),
                         max_seconds=5, fps=50)
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
