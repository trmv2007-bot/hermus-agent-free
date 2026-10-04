"""Failure-injection tests for the reliability control plane.

These tests intentionally simulate failures at subsystem boundaries rather than
calling real external services. They verify that recovery metadata is durable,
side effects are deduplicated, and corrupted recovery state is rejected.
"""

import time

from core.reliability import CheckpointStore, CircuitBreaker, IdempotencyStore, RecoverySnapshotStore


def test_provider_failure_opens_circuit_and_recovers():
    circuit = CircuitBreaker("model-provider", threshold=2, reset_after=0.01)
    circuit.failure()
    circuit.failure()
    assert not circuit.allow()
    time.sleep(0.02)
    assert circuit.allow()
    circuit.success()
    assert circuit.snapshot()["state"] == "closed"


def test_duplicate_side_effect_is_replayed_from_receipt(tmp_path):
    store = IdempotencyStore(tmp_path / "idempotency.json")
    first, _ = store.begin("email:abc", "send_email")
    assert first
    store.finish("email:abc", {"message_id": "m-1"})

    second, receipt = store.begin("email:abc", "send_email")
    assert not second
    assert receipt.status == "succeeded"
    assert receipt.result == {"message_id": "m-1"}


def test_interrupted_run_can_resume_from_verified_checkpoint(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoints.json")
    store.save("mission-7", "execute", {"completed_steps": [1, 2]}, verified=True)
    checkpoint = store.latest("mission-7")
    assert checkpoint is not None
    assert checkpoint["verified"] is True
    assert checkpoint["state"]["completed_steps"] == [1, 2]


def test_corrupted_snapshot_is_never_restored(tmp_path):
    source = tmp_path / "state.json"
    source.write_text("known-good", encoding="utf-8")
    snapshots = RecoverySnapshotStore(tmp_path / "snapshots")
    manifest = snapshots.snapshot_paths([source], label="chaos")

    snapshot_file = tmp_path / "snapshots" / manifest["id"] / "state.json"
    snapshot_file.write_text("CORRUPTED", encoding="utf-8")

    assert snapshots.verify(manifest["id"])["valid"] is False
    result = snapshots.restore(manifest["id"], tmp_path / "restore")
    assert result["success"] is False


def test_resource_guard_degradation_threshold_is_explicit():
    # This test documents the contract without requiring psutil or a particular
    # runner's memory pressure profile.
    from core.reliability import ResourceGuard

    snapshot = ResourceGuard().snapshot()
    assert "disk_free_ratio" in snapshot
    assert "degraded" in snapshot
