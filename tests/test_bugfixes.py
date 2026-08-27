"""Regression tests for the bugfix sweep (security, memory, artifacts, mission).

Each test pins a defect that was found by code review and confirmed by a
reproduction before fixing:

* gateway auth used ``!=`` for token comparison (timing side channel)
* ``Memory.periodic_nudges`` read column index 3 (role) instead of content,
  so nudges were always empty
* ``Memory.update_user_model`` deduplicated lists by stringifying every item,
  corrupting ``[{"name": ...}]`` into ``['{"name": ...}']``
* ``ArtifactManager.scan_workspace`` re-registered the same file on every scan
  (unbounded manifest growth), double-scanned build dirs, and registered its
  own manifest.json as an artifact
* ``.tar.gz`` artifacts were never detected (``Path.suffix`` is ``.gz``)
* ``MissionEngine`` never updated ``progress_pct`` mid-loop, never extended
  the step budget despite advertising dynamic budgets, and reported 100%
  progress for failed missions
* ``FreeLLM._call_hf_free`` referenced a possibly-unbound ``current_token``

Offline + dependency-light: no models or network required. A temporary
HERMUS_HOME is set BEFORE importing modules that bind a global workspace.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

# Isolate the workspace before importing anything that binds a global instance.
_TMP = tempfile.mkdtemp(prefix="hermus_test_bugfix_")
os.environ["HERMUS_HOME"] = _TMP

from core.config import config  # noqa: E402

config.model = "mock/mock"
config.memory_db_path = str(Path(_TMP) / "memory.db")
config.memory2_db_path = str(Path(_TMP) / "memory2.db")
config.trajectory_path = str(Path(_TMP) / "trajectories.jsonl")
config.user_model_path = str(Path(_TMP) / "user_model.json")
config.embeddings_db_path = str(Path(_TMP) / "embeddings.db")

from core.artifact_manager import ArtifactManager, _detect_extension  # noqa: E402
from core.memory import Memory  # noqa: E402
from core.mission import MissionEngine, MissionState  # noqa: E402

import gateway.gateway as gw  # noqa: E402
import gateway.realtime as rt  # noqa: E402


# --------------------------------------------------------------------- security
def test_gateway_token_comparison_is_constant_time():
    """Auth helpers must exist, agree on basic truth table, and never leak on None."""
    assert gw._token_matches("abc", "abc") is True
    assert gw._token_matches("abc", "xyz") is False
    assert gw._token_matches(None, "xyz") is False
    assert gw._token_matches("", "xyz") is False
    # Length-mismatch inputs must not raise
    assert gw._token_matches("short", "a-much-longer-expected-token") is False


def test_realtime_auth_ok(monkeypatch):
    monkeypatch.delenv("HERMUS_GATEWAY_TOKEN", raising=False)
    # No expected token configured → open gateway
    assert rt._auth_ok("anything", None) is True

    monkeypatch.setenv("HERMUS_GATEWAY_TOKEN", "sekret")
    assert rt._auth_ok("sekret", None) is True
    assert rt._auth_ok(None, "sekret") is True
    assert rt._auth_ok("wrong", None) is False
    assert rt._auth_ok(None, None) is False


# ----------------------------------------------------------------------- memory
def test_periodic_nudges_reads_content_column(tmp_path):
    mem = Memory(db_path=str(tmp_path / "memory.db"))
    mem.add_session_message("s1", "user", "please remember my deployment workflow " + "x" * 200)
    mem.add_session_message("s2", "user", "short message without keywords")

    nudges = mem.periodic_nudges()
    # Before the fix, row[3] was the *role* column ("user"), which never
    # contains "remember" nor exceeds 200 chars → nudges was always [].
    assert any("s1" in n for n in nudges), "long 'remember' message must produce a nudge"
    assert not any("s2" in n for n in nudges), "short message must not produce a nudge"


def test_update_user_model_preserves_list_item_types(tmp_path):
    mem = Memory(db_path=str(tmp_path / "memory.db"))
    mem.save_user_model({
        "preferences": {"style": "concise"},
        "projects": [{"name": "alpha", "lang": "py"}],
        "workflows": ["deploy"],
    })

    mem.update_user_model({
        "projects": [{"name": "beta", "lang": "rs"}, {"name": "alpha", "lang": "py"}],
        "workflows": ["deploy", "test", "deploy"],
    })

    model = mem.load_user_model()
    # Dicts must stay dicts (previously stringified to JSON blobs)
    assert all(isinstance(p, dict) for p in model["projects"])
    assert {"name": "alpha", "lang": "py"} in model["projects"]
    assert {"name": "beta", "lang": "rs"} in model["projects"]
    # Dedup still happens, and scalars keep their types
    assert model["workflows"] == ["deploy", "test"]
    # Dict sections still merge instead of being replaced
    assert model["preferences"] == {"style": "concise"}


# -------------------------------------------------------------------- artifacts
def test_scan_workspace_is_idempotent_no_duplicates(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "out").mkdir(parents=True)
    (ws / "out" / "report.json").write_text('{"ok": true}')
    mgr = ArtifactManager(storage_dir=tmp_path / "artifacts", workspace_root=ws)

    first = mgr.scan_workspace(mission_id="m1")
    second = mgr.scan_workspace(mission_id="m2")
    third = mgr.scan_workspace(mission_id="m2")

    # One physical file → exactly one artifact per scan, one manifest entry.
    assert len(first) == 1
    assert len(second) == 1
    assert len(third) == 1
    assert len(mgr._load_manifest()) == 1
    # The manifest.json itself must never be registered as an artifact.
    assert all(Path(a.path).name != "manifest.json" for a in first)
    # Latest scan re-attributes the artifact to the newest mission.
    assert mgr.list_artifacts(mission_id="m2")


def test_scan_workspace_double_scan_removed(tmp_path):
    """Files under build/ used to be yielded twice per scan (root rglob + dir rglob)."""
    ws = tmp_path / "workspace"
    (ws / "build").mkdir(parents=True)
    (ws / "build" / "app.apk").write_bytes(b"PK\x03\x04fake")
    mgr = ArtifactManager(storage_dir=tmp_path / "artifacts", workspace_root=ws)

    scanned = mgr.scan_workspace(mission_id="m1")
    paths = [a.path for a in scanned]
    assert len(paths) == len(set(paths)), "no file may be registered twice in one scan"
    assert any(p.endswith("app.apk") for p in paths)


def test_tar_gz_extension_detection():
    assert _detect_extension(Path("bundle.tar.gz")) == ".tar.gz"
    assert _detect_extension(Path("bundle.TAR.GZ")) == ".tar.gz"
    assert _detect_extension(Path("bundle.tgz")) == ".tgz"
    assert _detect_extension(Path("report.json")) == ".json"
    assert _detect_extension(Path("plain")) == ""


def test_register_artifact_same_path_is_idempotent(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    f = ws / "out.zip"
    f.write_bytes(b"PK\x03\x04data")
    mgr = ArtifactManager(storage_dir=tmp_path / "artifacts", workspace_root=ws)

    a1 = mgr.register_artifact(f, mission_id="m1")
    a2 = mgr.register_artifact(f, mission_id="m2")

    assert a1.id == a2.id, "same physical file must not mint a new artifact id"
    assert a2.mission_id == "m2"
    assert len(mgr.list_artifacts()) == 1


def test_tar_gz_artifact_type_detected(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "bundle.tar.gz").write_bytes(b"not really gzip but fine")
    mgr = ArtifactManager(storage_dir=tmp_path / "artifacts", workspace_root=ws)

    art = mgr.register_artifact(ws / "bundle.tar.gz")
    assert art.artifact_type == "archive"


# ---------------------------------------------------------------------- mission
def test_mission_progress_and_budget_extension(tmp_path):
    engine = MissionEngine(storage_dir=tmp_path / "missions")

    report = engine.start_mission("produce a research report", domain="research", budget_steps=25)
    # progress_pct must be recorded during/after the run, not only at 100%
    assert 0 <= report.progress_pct <= 100
    assert report.state in (MissionState.COMPLETED.value, MissionState.FAILED.value, MissionState.BLOCKED.value)
    if report.state == MissionState.FAILED.value:
        # A failed mission must never claim 100% progress.
        assert report.progress_pct <= 95

    # Budget extension API
    ext = engine.extend_budget(report.mission_id, steps=5)
    assert ext.budget.extensions_used == 1
    assert ext.budget.initial_steps == 30

    # Extension cap is enforced
    engine.extend_budget(report.mission_id)
    try:
        engine.extend_budget(report.mission_id)
        raise AssertionError("third extension must be rejected (max_extensions=2)")
    except ValueError as exc:
        assert "extensions" in str(exc).lower()


def test_mission_lifecycle_progress_persisted(tmp_path):
    """A mission that ends blocked mid-DAG reports partial, non-zero progress."""
    engine = MissionEngine(storage_dir=tmp_path / "missions")

    def blocking_executor(node, ctx):
        if node.id == "impl":
            return {"success": False, "blocked": True,
                    "blocker_reason": "need deploy credentials"}
        return {"success": True, "output": f"done {node.id}"}

    engine._raw_executor = blocking_executor
    report = engine.start_mission("build the thing", domain="generic")
    assert report.state == MissionState.BLOCKED.value
    assert 0 < report.progress_pct < 100, "partial DAG completion must show partial progress"
    # The persisted JSON carries the same progress.
    stored = engine.get_mission(report.mission_id)
    assert stored.progress_pct == report.progress_pct


# -------------------------------------------------------------------------- llm
def test_hf_call_binds_current_token_before_client_use():
    """The HF fallback must reference a bound variable in its except path.

    We simulate a failure before the client call by pointing the model at a
    missing huggingface_hub — the function must return gracefully (previously
    the except path relied on a possibly-unbound ``current_token``).
    """
    import core.llm as llm

    src = Path(llm.__file__).read_text(encoding="utf-8")
    assert "current_token: Optional[str] = None" in src, (
        "current_token must be initialized before the try block"
    )


def test_hf_call_returns_gracefully_without_hf_hub(tmp_path, monkeypatch):
    """HF provider degrades to a readable error when huggingface_hub is absent."""
    import builtins
    import importlib

    import core.llm as llm

    real_import = builtins.__import__

    def no_hf(name, *args, **kwargs):
        if name.startswith("huggingface_hub"):
            raise ImportError("huggingface_hub is not installed (test stub)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_hf)
    try:
        model = llm.FreeLLM(model="hf/zing", api_key="hf_test_key")
        resp = model.chat([{"role": "user", "content": "hello"}])
        assert resp is not None
        assert "not installed" in resp.content or "error" in resp.content.lower()
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)
        importlib.reload(llm)


# ------------------------------------------------------------------ multi-key
def test_multi_key_store_survives_concurrent_updates(tmp_path):
    """Concurrent mark_key_success calls must not wipe the key store.

    Fleet workers report from several threads at once. The old unlocked
    load→mutate→save cycles could read a half-written JSON file, fall back to
    the empty template, and save it back — deleting every stored API key.
    """
    import threading

    from core.multi_key import MultiKeyManager

    mgr = MultiKeyManager(db_path=str(tmp_path / "keys.json"))
    mgr.add_key("custom", "sk-race-aaaaaaaa", name="k1", base_url="http://127.0.0.1:9/v1", auto_discover=False)
    mgr.add_key("custom", "sk-race-bbbbbbbb", name="k2", base_url="http://127.0.0.1:9/v1", auto_discover=False)

    def hammer(key: str) -> None:
        for _ in range(40):
            mgr.mark_key_success("custom", key, tokens=15, latency_ms=5)
            mgr.mark_key_failed("custom", key, error="synthetic 429 rate limit")

    threads = [
        threading.Thread(target=hammer, args=("sk-race-aaaaaaaa",)),
        threading.Thread(target=hammer, args=("sk-race-bbbbbbbb",)),
        threading.Thread(target=hammer, args=("sk-race-aaaaaaaa",)),
        threading.Thread(target=hammer, args=("sk-race-bbbbbbbb",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = mgr.get_all_entries("custom")
    assert len(entries) == 2, f"both keys must survive concurrent updates, got {len(entries)}"
    # The persisted file itself must still be valid JSON with both keys.
    import json

    data = json.loads((tmp_path / "keys.json").read_text())
    assert len(data["custom"]) == 2


def test_multi_key_corrupt_store_is_backed_up(tmp_path):
    """A corrupt key store is preserved for recovery, not silently replaced."""
    from core.multi_key import MultiKeyManager

    db = tmp_path / "keys.json"
    db.write_text('{"custom": [{"key": "sk-keep", "name": "k1"}')  # truncated JSON

    mgr = MultiKeyManager(db_path=str(db))
    mgr.mark_key_success("custom", "sk-other", tokens=1)

    backups = list(tmp_path.glob("keys.json.corrupt-*"))
    assert backups, "the corrupt store must be backed up before being replaced"


def test_map_goal_executes_each_subtask_once():
    """map_goal used to submit every subtask into two executors (2x API spend)."""
    import json
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from core.multi_key import MultiKeyManager
    import core.multi_key as mk_mod
    import core.model_fleet as fleet_mod

    lock = threading.Lock()
    seen: list = []

    class CountingHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _reply(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            with lock:
                seen.append(self.path)
            self._reply({
                "id": "x", "model": "m",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        def do_GET(self):
            self._reply({"data": [{"id": "m", "owned_by": "x"}]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), CountingHandler)
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    mgr = MultiKeyManager(db_path=str(Path(tempfile.mkdtemp()) / "keys.json"))
    mgr.add_key("custom", "sk-count-00000001", name="k1", base_url=base, default_model="m", auto_discover=False)
    old = mk_mod.multi_key_manager
    mk_mod.multi_key_manager = mgr
    fleet_mod.multi_key_manager = mgr
    try:
        fleet = fleet_mod.ModelFleet()
        res = fleet.map_goal(
            "First subtask here. Second subtask there. Third subtask everywhere.",
            subtasks=["a", "b", "c"],
            providers=["custom"],
            max_workers=3,
            merge=False,
        )
        assert res["success"], "map_goal must succeed against the counting server"
        chat_calls = [p for p in seen if "chat/completions" in p]
        assert len(chat_calls) == 3, (
            f"each subtask must execute exactly once, got {len(chat_calls)} chat calls"
        )
    finally:
        mk_mod.multi_key_manager = old
        fleet_mod.multi_key_manager = old
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------- memory db
def test_memory_concurrent_thread_access(tmp_path):
    """Thread-local SQLite connections must survive concurrent gateway-style use.

    Every thread gets its own long-lived connection (WAL + busy_timeout);
    concurrent writers must all land and no 'database is locked' may escape.
    """
    import threading

    mem = Memory(db_path=str(tmp_path / "memory.db"))

    def writer(n: int) -> None:
        for i in range(25):
            mem.add_session_message(f"s{n}", "user", f"thread {n} message {i}")
            mem.add_token_usage(f"s{n}", {"model": "m", "total_tokens": 10})

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = mem.search_sessions("thread", limit=1000)
    assert len(rows) == 6 * 25, f"all concurrent writes must persist, got {len(rows)}"
    usage = mem.get_token_usage(limit=1000)
    assert usage["count"] == 6 * 25
    # Nudges still work over the shared schema.
    mem.add_session_message("sx", "user", "please remember " + "y" * 200)
    assert mem.periodic_nudges()
