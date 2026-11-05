from core.personal_os import PersonalOS


def test_personal_os_persists_tasks_and_builds_briefing(tmp_path):
    os_layer = PersonalOS(tmp_path / "personal_os.json")
    task = os_layer.add_task("Ship the dashboard", priority="high", area="work", project="hermus")
    os_layer2 = PersonalOS(tmp_path / "personal_os.json")
    assert os_layer2.list_tasks(project="hermus")[0]["title"] == "Ship the dashboard"
    briefing = os_layer2.briefing(area="work")
    assert briefing["priority_tasks"][0]["id"] == task["id"]
    assert briefing["open_task_count"] == 1


def test_personal_os_completion_is_durable(tmp_path):
    os_layer = PersonalOS(tmp_path / "personal_os.json")
    task = os_layer.add_task("Review changes")
    done = os_layer.complete_task(task["id"])
    assert done["status"] == "done"
    restored = PersonalOS(tmp_path / "personal_os.json")
    assert restored.list_tasks(status="done")[0]["id"] == task["id"]


def test_personal_os_execute_uses_canonical_queue(tmp_path, monkeypatch):
    os_layer = PersonalOS(tmp_path / "personal_os.json")
    task = os_layer.add_task("Run the verification pass", priority="urgent")

    class Job:
        id = "job_test"
        run_id = "run_test"

    class Queue:
        def submit(self, kind, payload, **kwargs):
            assert kind == "runtime.turn"
            assert payload["personal_os_task_id"] == task["id"]
            return Job()

    import gateway.queue

    monkeypatch.setattr(gateway.queue, "job_queue", Queue())
    result = os_layer.execute_task(task["id"])
    assert result["success"] is True
    assert result["run_id"] == "run_test"
