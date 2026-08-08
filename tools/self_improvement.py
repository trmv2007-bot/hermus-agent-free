"""Self-Improvement Tools - Free - Reflection when idle, sees mistakes, searches improvements, fixes in background"""

from typing import Dict

def run_self_improvement_reflection() -> Dict:
    """Run self-improvement reflection now - goes through reflections, sees mistakes, searches how to improve, fixes itself in background - free"""
    try:
        from core.self_improvement import self_improvement
        result = self_improvement.run_idle_reflection(force=True)
        return result
    except Exception as e:
        return {"error": str(e)}

def get_self_improvement_status() -> Dict:
    """Get status for hideable panel to show what it is doing - free"""
    try:
        from core.self_improvement import self_improvement
        return self_improvement.get_status()
    except Exception as e:
        return {"error": str(e)}

def start_self_improvement_background_checker() -> Dict:
    """Start background idle checker that triggers reflection when idle - free"""
    try:
        from core.self_improvement import self_improvement
        return self_improvement.start_background_idle_checker()
    except Exception as e:
        return {"error": str(e)}

def stop_self_improvement_background_checker() -> Dict:
    """Stop background checker"""
    try:
        from core.self_improvement import self_improvement
        return self_improvement.stop_background_checker()
    except Exception as e:
        return {"error": str(e)}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_self_improvement_reflection",
            "description": "Run self-improvement reflection now - goes through reflections and sees what mistakes it did during work, searches how to improve itself, fixes itself in background - free - for when given work is done as it goes idle",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_self_improvement_status",
            "description": "Get status for hideable panel to show what self-improvement is doing - reflection, mistakes, improvements, fixes in background - free",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_self_improvement_background_checker",
            "description": "Start background idle checker that triggers reflection when idle every 30 sec - free - when given work is done as it goes idle, it should go through reflections and see what mistakes it did, search how to improve, fix itself in background",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
]

TOOL_MAP = {
    "run_self_improvement_reflection": run_self_improvement_reflection,
    "get_self_improvement_status": get_self_improvement_status,
    "start_self_improvement_background_checker": start_self_improvement_background_checker,
    "stop_self_improvement_background_checker": stop_self_improvement_background_checker,
}
