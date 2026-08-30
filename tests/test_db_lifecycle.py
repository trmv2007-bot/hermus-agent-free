"""SQLite lifecycle: no connection may outlive the process.

Regression coverage for the shutdown wall of

    <sys>:0: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x...>

which appeared when ``Ctrl+C`` stopped the gateway: long-lived handles
(``core.memory``'s per-thread cache, ``memory2``'s shared handle, the web-read
cache) were never closed, so CPython finalised them during GC and reported each
one.  The registry closes them in the lifespan ``finally:`` block, and owners
reopen on the next access instead of touching a closed database.
"""
from __future__ import annotations

import sqlite3
import threading
import warnings

import pytest

from core import db_registry
from core.db_registry import ConnectionRegistry, close_all, db_registry, open_db, using


@pytest.fixture
def registry():
    reg = ConnectionRegistry()
    yield reg
    reg.close_all("test-teardown")


# ---------------------------------------------------------------------------
# Registry basics
# ---------------------------------------------------------------------------
def test_open_db_registers_and_close_all_releases(tmp_path, registry):
    conn = registry.register(open_db(tmp_path / "a.db", owner="test"), path="a.db", owner="test")
    assert registry.stats()["open"] == 1
    report = registry.close_all("shutdown")
    assert report["closed"] == 1
    assert report["errors"] == []
    assert registry.stats()["open"] == 0
    # The handle really is closed.
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_close_all_is_idempotent(tmp_path, registry):
    registry.register(open_db(tmp_path / "a.db", owner="test"), path="a.db", owner="test")
    registry.close_all()
    second = registry.close_all()
    assert second["closed"] == 0
    assert second["already_closed"] == 0


def test_unregister_stops_tracking(tmp_path, registry):
    conn = registry.register(open_db(tmp_path / "a.db", owner="x"), path="a.db", owner="x")
    registry.unregister(conn)
    assert registry.stats()["open"] == 0
    conn.close()


def test_using_closes_even_when_the_body_raises(tmp_path, registry):
    path = tmp_path / "b.db"
    with pytest.raises(RuntimeError):
        with using(path, owner="test") as conn:
            conn.execute("CREATE TABLE t (x INTEGER)")
            raise RuntimeError("boom")
    # Nothing left open, and the table still exists (the connection was real).
    assert registry.is_open(conn) is False
    check = sqlite3.connect(path)
    assert check.execute("SELECT name FROM sqlite_master WHERE name='t'").fetchone()
    check.close()


def test_using_commits_only_when_the_caller_commits(tmp_path):
    path = tmp_path / "c.db"
    with using(path, owner="t") as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
    with using(path, owner="t") as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# The real leak: long-lived owners across threads + shutdown
# ---------------------------------------------------------------------------
def test_memory_handles_are_released_by_shutdown_sweep(tmp_path):
    """The exact shape that leaked: per-thread Memory connections."""
    from core.memory import Memory

    close_all("clear-any-earlier-handles")
    store = Memory(db_path=str(tmp_path / "mem.db"))
    threads = []

    def work():
        store.add_session_message("s", "user", "hello")

    for _ in range(4):
        thread = threading.Thread(target=work)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()

    open_before = db_registry.stats()["open"]
    assert open_before >= 5, f"main + 4 worker handles expected, got {open_before}"
    assert {h["owner"] for h in db_registry.stats()["handles"]} == {"memory"}

    report = close_all("shutdown")
    assert report["closed"] == open_before
    assert report["errors"] == []
    assert db_registry.stats()["open"] == 0


def test_memory_reopens_after_a_shutdown_sweep(tmp_path, monkeypatch):
    """A closed handle must not become 'Cannot operate on a closed database'."""
    from core.memory import Memory

    store = Memory(db_path=str(tmp_path / "mem2.db"))
    store.add_session_message("s", "user", "before shutdown")
    close_all("simulated gateway shutdown")
    # Same singleton, same thread — must transparently reopen.
    store.add_session_message("s", "user", "after shutdown")
    hits = store.search_sessions("after shutdown")
    assert hits, "memory must keep working after the registry closed its handle"


def test_memory2_rebuilds_its_retriever_after_a_shutdown_sweep(tmp_path):
    """The retriever caches the handle; a stale one would silently return nothing."""
    from core.memory2 import MemoryStore

    store = MemoryStore(db_path=str(tmp_path / "m2.db"))
    store.remember("semantic", "the vault holds the database password")
    assert store.remember("semantic", "second note about the vault")["success"]
    first = store.retriever()
    assert first is not None

    close_all("simulated gateway shutdown")
    store.remember("semantic", "third note about the vault")
    assert store.retriever() is not first, "retriever must be rebuilt over the fresh handle"

    results = store.retriever().search("vault database password", limit=5)
    assert results, "hybrid search must still find rows after a shutdown sweep"


def test_close_all_reports_generation_bump(tmp_path):
    before = db_registry.generation
    open_db(tmp_path / "gen.db", owner="gen")
    report = close_all("gen-test")
    assert report["generation"] == before + 1
    assert db_registry.generation == before + 1


def test_no_resource_warning_after_shutdown(tmp_path):
    """With the registry in place, GC finds nothing left unclosed."""
    import gc

    store_conn = open_db(tmp_path / "leak.db", owner="leak-test")
    store_conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    close_all("leak-test")
    del store_conn
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        gc.collect()
    leaked = [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]
    assert not leaked, leaked


# ---------------------------------------------------------------------------
# Gateway wiring
# ---------------------------------------------------------------------------
def test_gateway_shutdown_closes_sqlite_handles():
    """The lifespan finally: block must call the registry."""
    from pathlib import Path

    source = Path("gateway/gateway.py").read_text(encoding="utf-8")
    assert "from core.db_registry import close_all as _close_dbs" in source
    assert "_close_dbs(\"gateway_shutdown\")" in source
    # ...and must also stop a local engine it started.
    assert "nollama_manager.stop_if_managed()" in source


def test_internet_eyes_cache_db_is_registered(tmp_path, monkeypatch):
    import tools.internet_eyes as eyes

    monkeypatch.setattr(eyes, "_WEB_CACHE_DB", None)
    monkeypatch.setattr(eyes.Path, "cwd", lambda: tmp_path)
    conn = eyes._get_web_cache_db()
    assert db_registry.is_open(conn) is True
    close_all("internet-eyes-test")
    assert db_registry.is_open(conn) is False
