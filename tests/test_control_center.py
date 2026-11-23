from pathlib import Path

from core.connectors import ConnectorContext, IntegrationControlCenter
from core.world_model import WorldModel


def test_control_center_installs_all_connectors_disabled_by_default(tmp_path):
    center = IntegrationControlCenter(context=ConnectorContext(world=WorldModel()))
    center.install_defaults(workspace_root=Path(tmp_path))
    overview = center.overview()
    names = {item["name"] for item in overview["connectors"]}
    # The desktop connector joined the built-ins with read-only PC awareness;
    # it installs disabled like every other connector here.
    assert {"runtime", "filesystem", "desktop", "calendar", "email", "github", "wallet", "hosting"} <= names
    assert overview["disabled"] == 8


def test_control_center_can_enable_and_refresh_local_connectors(tmp_path):
    center = IntegrationControlCenter(context=ConnectorContext(world=WorldModel()))
    center.install_defaults(workspace_root=Path(tmp_path))
    center.registry.enable("runtime")
    center.registry.enable("filesystem")
    results = center.refresh()
    assert {item["connector"] for item in results} == {"runtime", "filesystem"}
    assert center.overview()["connected"] == 2
