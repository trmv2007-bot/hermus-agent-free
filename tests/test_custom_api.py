"""Test Custom API Feature - Free"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

def test_custom_api():
    from core.custom_api import custom_api_manager
    # Clean
    custom_api_manager._save([])

    # Add example free API - jsonplaceholder free
    api_def = {
        "name": "jsonplaceholder_post",
        "description": "Get a fake post by ID from jsonplaceholder free API for testing",
        "url": "https://jsonplaceholder.typicode.com/posts/{id}",
        "method": "GET",
        "parameters": {
            "id": {"type": "string", "description": "Post ID 1-100"}
        },
        "headers": {},
        "auth": {"type": "none"}
    }
    result = custom_api_manager.add_api(api_def)
    assert result["success"]
    print(f"✅ Added custom API: {result}")

    # List
    apis = custom_api_manager.list_apis()
    assert len(apis) == 1
    print(f"✅ List: {len(apis)}")

    # Get tool definitions
    tools = custom_api_manager.get_tool_definitions()
    assert len(tools) == 1
    print(f"✅ Tool definitions: {tools[0]['function']['name']}")

    # Execute
    exec_result = custom_api_manager.execute_api("jsonplaceholder_post", {"id": "1"})
    assert exec_result["success"]
    assert exec_result["status_code"] == 200
    print(f"✅ Execute custom API: {exec_result['data_str'][:200]}")

    # Test via agent
    from core.agent import HermusAgent
    agent = HermusAgent(model="mock/mock")
    # Check tools include custom
    tool_names = [t["function"]["name"] for t in agent.tools]
    assert "jsonplaceholder_post" in tool_names
    print(f"✅ Agent includes custom API tool: {tool_names}")

    # Cleanup
    custom_api_manager.remove_api("jsonplaceholder_post")
    print(f"✅ Custom API feature works - 100% free, no paywall!")

if __name__ == "__main__":
    test_custom_api()
