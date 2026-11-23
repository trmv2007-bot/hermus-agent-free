from core.distributed import DistributedCoordinator


def test_distributed_node_registry_persists(tmp_path):
    coordinator = DistributedCoordinator(tmp_path / "nodes.json")
    node = coordinator.register_node("GPU server", capabilities=["gpu", "vision"])
    restored = DistributedCoordinator(tmp_path / "nodes.json")
    assert restored.list_nodes()[0]["id"] == node["id"]
    assert "vision" in restored.list_nodes()[0]["capabilities"]


def test_distributed_assignment_routes_by_capability(tmp_path):
    coordinator = DistributedCoordinator(tmp_path / "nodes.json")
    node = coordinator.register_node("Vision node", capabilities=["vision"])
    result = coordinator.assign("job_1", capability="vision")
    assert result["success"] is True
    assert result["assignment"]["node_id"] == node["id"]


def test_distributed_does_not_route_missing_capability(tmp_path):
    coordinator = DistributedCoordinator(tmp_path / "nodes.json")
    coordinator.register_node("CPU node", capabilities=["cpu"])
    result = coordinator.assign("job_2", capability="gpu")
    assert result["success"] is False
    assert result["error"] == "no_healthy_node_with_capability"


def test_stale_nodes_are_not_selected(tmp_path, monkeypatch):
    coordinator = DistributedCoordinator(tmp_path / "nodes.json", heartbeat_timeout=1)
    coordinator.register_node("Old node", capabilities=["vision"])

    # Move the clock past the heartbeat window instead of relying on real time
    # elapsing between registering and assigning: staleness is
    # `now - last_heartbeat > timeout`, so on a fast runner both calls landed in
    # the same tick and the node stayed healthy.
    import core.distributed as distributed

    real_time = distributed.time.time
    monkeypatch.setattr(distributed.time, "time", lambda: real_time() + 5)

    result = coordinator.assign("job_3", capability="vision")
    assert result["success"] is False
