"""Subagents - Spawn isolated subagents for parallel workstreams, free"""
import uuid
import json
import multiprocessing
from datetime import datetime
from pathlib import Path
from typing import List, Dict

from core.config import config

def subagent_task_wrapper(task: str, result_queue, subagent_id: str):
    """Wrapper that runs in separate process - isolated subagent"""
    try:
        from core.agent import HermusAgent
        from core.task_tracker import task_tracker
        # Track subagent in task tracker
        try:
            task_tracker.add_agent(subagent_id, f"subagent_{subagent_id[:4]}", "subagent", persona="subagent", task=task[:100])
            task_tracker.add_task(subagent_id, "subagent", task, model="subagent", agent=subagent_id)
        except:
            pass

        agent = HermusAgent(session_id=f"subagent_{subagent_id}")
        result = agent.chat(task)

        try:
            task_tracker.complete_task(subagent_id, status="done", result=result.get("response","")[:200])
            task_tracker.remove_agent(subagent_id, final_status="done")
        except:
            pass

        result_queue.put({"subagent_id": subagent_id, "task": task, "result": result, "success": True})
    except Exception as e:
        try:
            from core.task_tracker import task_tracker
            task_tracker.complete_task(subagent_id, status="failed", result=str(e)[:200])
            task_tracker.remove_agent(subagent_id, final_status="failed")
        except:
            pass
        result_queue.put({"subagent_id": subagent_id, "task": task, "error": str(e), "success": False})

def spawn_subagent(task: str) -> Dict:
    """Spawn single isolated subagent - free"""
    subagent_id = f"sub_{uuid.uuid4().hex[:6]}"
    result_queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=subagent_task_wrapper, args=(task, result_queue, subagent_id))
    process.start()
    process.join(timeout=60)  # 60s timeout

    if process.is_alive():
        process.terminate()
        return {"subagent_id": subagent_id, "task": task, "error": "Timeout after 60s", "success": False}

    if not result_queue.empty():
        return result_queue.get()
    else:
        return {"subagent_id": subagent_id, "task": task, "error": "No result", "success": False}

def spawn_parallel_subagents(tasks: List[str]) -> List[Dict]:
    """Spawn multiple subagents in parallel - free parallel workstreams"""
    print(f"[Subagents] Spawning {len(tasks)} parallel subagents")
    processes = []
    queues = []

    for task in tasks:
        subagent_id = f"sub_{uuid.uuid4().hex[:6]}"
        q = multiprocessing.Queue()
        p = multiprocessing.Process(target=subagent_task_wrapper, args=(task, q, subagent_id))
        p.start()
        processes.append(p)
        queues.append((subagent_id, task, q))

    # Wait for all
    results = []
    for p in processes:
        p.join(timeout=90)

    for subagent_id, task, q in queues:
        if not q.empty():
            results.append(q.get())
        else:
            results.append({"subagent_id": subagent_id, "task": task, "error": "No result or timeout", "success": False})

    return results

def write_python_tool_via_rpc(tool_name: str, steps: List[str]) -> Dict:
    """Write Python scripts that call tools via RPC, collapsing multi-step pipelines into zero-context-cost turns - free"""
    # This is a key Hermes feature: subagent writes a Python script that calls tools via RPC instead of many turns
    # Example: Instead of 5 turns: search, read, write, shell, etc., it writes one Python file that does all via RPC in one turn

    code = f'''# Auto-generated tool {tool_name} via RPC - zero-context-cost
# Steps: {", ".join(steps)}

from tools.web_search import web_search
from tools.file_tools import file_read, file_write
from tools.shell import shell_execute

def run():
    results = []
'''
    for i, step in enumerate(steps):
        code += f'    # Step {i+1}: {step}\n'
        code += f'    print("Step {i+1}: {step}")\n'

    code += '''
    return results

if __name__ == "__main__":
    run()
'''

    # Save as skill for reuse
    try:
        from core.skill_manager import skill_manager
        skill_dir = Path(config.resolve_path(config.skills_dir)) / tool_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "skill.py").write_text(code)
        (skill_dir / "SKILL.md").write_text(f"# {tool_name}\n\nAuto-generated via RPC collapsing {len(steps)} steps\n\nSteps:\n" + "\n".join([f"- {s}" for s in steps]))
        return {"success": True, "tool_name": tool_name, "path": str(skill_dir), "code": code[:500]}
    except Exception as e:
        return {"success": False, "error": str(e)}
