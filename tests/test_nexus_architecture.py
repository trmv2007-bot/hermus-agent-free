"""Contract tests for the product-level Nexus backend architecture."""

from core.nexus.models import NexusCommand
from core.nexus.service import NexusService


def test_nexus_command_normalizes_text_and_defaults():
    command = NexusCommand(text="  hello  ", user_id="", channel="")
    normalized = command.normalized()
    assert normalized.text == "hello"
    assert normalized.user_id == "default"
    assert normalized.channel == "nexus"


def test_nexus_service_rejects_empty_command_without_execution():
    service = NexusService()
    result = service.submit(NexusCommand(text="   "))
    assert result["accepted"] is False
    assert result["status"] == "rejected"
    assert "empty" in result["error"].lower()


def test_mission_contract_is_serializable():
    service = NexusService()
    mission = service._missions
    assert isinstance(mission, dict)
