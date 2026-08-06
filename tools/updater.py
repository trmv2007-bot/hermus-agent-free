"""Updater Tools - Free - Check for GitHub updates and show in dashboard and CLI"""

from typing import Dict

def check_update() -> Dict:
    """Check if update available from GitHub - shows update in dashboard and CLI - free"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        result = updater.check_for_updates()
        return result
    except Exception as e:
        return {"error": str(e), "update_available": False}

def do_update() -> Dict:
    """Update from GitHub via git pull + pip install - like hermes update - free"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        result = updater.update()
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_local_commit() -> Dict:
    """Get local commit info"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        return updater.get_local_commit()
    except Exception as e:
        return {"error": str(e)}

def get_remote_commit() -> Dict:
    """Get remote latest commit from GitHub API - free, no API key for public repo"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        return updater.get_remote_commit()
    except Exception as e:
        return {"error": str(e)}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_update",
            "description": "Check if update available from GitHub - shows update in dashboard and CLI too - compares local commit vs remote GitHub latest, shows behind by count, remote message, author, date - free, no API key for public repos",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "do_update",
            "description": "Update from GitHub via git pull origin main + pip install -r requirements.txt - like hermes update command - free - pulls latest and reinstalls dependencies",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_local_commit",
            "description": "Get local current commit via git rev-parse HEAD - free",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_remote_commit",
            "description": "Get remote latest commit from GitHub API free no API key for public repo - via api.github.com/repos/owner/repo/commits/main",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
]

TOOL_MAP = {
    "check_update": check_update,
    "do_update": do_update,
    "get_local_commit": get_local_commit,
    "get_remote_commit": get_remote_commit,
}
