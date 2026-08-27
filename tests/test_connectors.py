from core.connectors import Connector, ConnectorContext, ConnectorRegistry, FilesystemConnector
from core.world_model import WorldModel


class DemoConnector(Connector):
    name = "demo"
    capabilities = ("demo.read",)

    def observe(self):
        return [{"subject": "demo", "predicate": "status", "value": "online", "confidence": 0.8}]

    def actions(self):
        return {"ping": lambda: {"ok": True}}


def test_registry_lifecycle_refresh_and_action():
    world = WorldModel()
    registry = ConnectorRegistry(ConnectorContext(world=world))
    registry.register(DemoConnector())
    assert registry.statuses()[0]["state"] == "disabled"
    registry.enable("demo")
    result = registry.refresh("demo")
    assert result[0]["facts"] == 1
    assert world.get("demo", "status").value == "online"
    assert registry.execute("demo", "ping")["ok"] is True


def test_filesystem_is_scoped_to_the_configured_root(tmp_path):
    (tmp_path / "hello.txt").write_text("hello")
    connector = FilesystemConnector(tmp_path)
    connector.enable()
    entries = connector.list_root()["entries"]
    assert {entry["name"] for entry in entries} == {"hello.txt"}
    assert connector.refresh()["success"] is True
