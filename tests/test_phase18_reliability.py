from core.reliability import CheckpointStore, CircuitBreaker, IdempotencyStore, RecoverySnapshotStore, RetryPolicy


def test_retry_policy_backoff_is_bounded():
    policy = RetryPolicy(max_attempts=4, base_delay=1, max_delay=3, jitter=0.2)
    assert policy.delay(1) <= 3
    assert policy.delay(4) <= 3


def test_circuit_breaker_opens_and_recovers():
    cb = CircuitBreaker("provider", threshold=2, reset_after=0.01)
    cb.failure()
    cb.failure()
    assert cb.allow() is False
    import time

    time.sleep(0.02)
    assert cb.allow() is True
    cb.success()
    assert cb.snapshot()["state"] == "closed"


def test_idempotency_persists(tmp_path):
    store = IdempotencyStore(tmp_path / "idempotency.json")
    fresh, rec = store.begin("k1", "send")
    assert fresh and rec.status == "in_progress"
    store.finish("k1", {"ok": True})
    restored = IdempotencyStore(tmp_path / "idempotency.json")
    assert restored.get("k1").result == {"ok": True}


def test_checkpoint_persists(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoints.json")
    cp = store.save("run1", "verify", {"step": 2}, verified=True)
    assert store.latest("run1")["id"] == cp["id"]


def test_snapshot_integrity(tmp_path):
    source = tmp_path / "state.json"
    source.write_text("hello", encoding="utf-8")
    store = RecoverySnapshotStore(tmp_path / "snapshots")
    manifest = store.snapshot_paths([source], label="test")
    assert store.verify(manifest["id"])["valid"] is True
    (tmp_path / "snapshots" / manifest["id"] / "state.json").write_text("tampered", encoding="utf-8")
    assert store.verify(manifest["id"])["valid"] is False
