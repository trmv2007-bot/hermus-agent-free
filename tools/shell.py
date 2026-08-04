"""Free Shell Tool - safe subprocess with timeout"""
import subprocess
from typing import Dict

def shell_execute(command: str, timeout: int = 10) -> Dict:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            "command": command,
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {"command": command, "error": f"Timeout after {timeout}s", "stdout": "", "stderr": ""}
    except Exception as e:
        return {"command": command, "error": str(e)}

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "shell_execute",
        "description": "Execute shell command safely with timeout, for file ops, git, etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command"},
                "timeout": {"type": "integer", "default": 10}
            },
            "required": ["command"]
        }
    }
}
