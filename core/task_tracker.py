"""Task Tracker - Tracks what agents/AI models are running or doing the task - free - for slide panel"""
import uuid
from datetime import datetime
from threading import Lock

class TaskTracker:
    """Tracks active agents, models, tasks for slide panel UI"""

    def __init__(self):
        self.active_agents: dict[str, dict] = {}  # agent_id -> info
        self.active_tasks: dict[str, dict] = {}  # task_id -> info
        self.completed_tasks: list[dict] = []  # recent completed
        self.lock = Lock()

    def add_agent(self, agent_id: str, name: str, model: str, persona: str = "", task: str = "") -> str:
        """Add running agent"""
        with self.lock:
            if not agent_id:
                agent_id = f"agent_{uuid.uuid4().hex[:6]}"
            info = {
                "agent_id": agent_id,
                "name": name,
                "model": model,
                "persona": persona[:100],
                "task": task[:200],
                "status": "running",
                "started": datetime.now().isoformat(),
                "last_update": datetime.now().isoformat()
            }
            self.active_agents[agent_id] = info
            print(f"[TaskTracker] Agent started: {name} ({model}) - {task[:50]}")
            return agent_id

    def update_agent(self, agent_id: str, status: str = None, task: str = None, progress: str = None):
        with self.lock:
            if agent_id in self.active_agents:
                if status:
                    self.active_agents[agent_id]["status"] = status
                if task:
                    self.active_agents[agent_id]["task"] = task[:200]
                if progress:
                    self.active_agents[agent_id]["progress"] = progress[:200]
                self.active_agents[agent_id]["last_update"] = datetime.now().isoformat()

    def remove_agent(self, agent_id: str, final_status: str = "done"):
        with self.lock:
            if agent_id in self.active_agents:
                info = self.active_agents.pop(agent_id)
                info["status"] = final_status
                info["ended"] = datetime.now().isoformat()
                self.completed_tasks.append(info)
                # Keep only last 20 completed
                if len(self.completed_tasks) > 20:
                    self.completed_tasks = self.completed_tasks[-20:]
                print(f"[TaskTracker] Agent {info['name']} {final_status}")

    def add_task(self, task_id: str, task_type: str, description: str, model: str = "", agent: str = "") -> str:
        """Add running task (subagent, cron, tool, etc)"""
        with self.lock:
            if not task_id:
                task_id = f"task_{uuid.uuid4().hex[:6]}"
            info = {
                "task_id": task_id,
                "type": task_type,  # subagent, cron, tool, multi-ai, custom_api, etc.
                "description": description[:300],
                "model": model,
                "agent": agent,
                "status": "running",
                "started": datetime.now().isoformat(),
                "progress": "starting"
            }
            self.active_tasks[task_id] = info
            return task_id

    def update_task(self, task_id: str, status: str = None, progress: str = None, result: str = None):
        with self.lock:
            if task_id in self.active_tasks:
                if status:
                    self.active_tasks[task_id]["status"] = status
                if progress:
                    self.active_tasks[task_id]["progress"] = progress[:300]
                if result:
                    self.active_tasks[task_id]["result"] = result[:500]
                self.active_tasks[task_id]["last_update"] = datetime.now().isoformat()

    def complete_task(self, task_id: str, status: str = "done", result: str = ""):
        with self.lock:
            if task_id in self.active_tasks:
                info = self.active_tasks.pop(task_id)
                info["status"] = status
                info["result"] = result[:500] if result else ""
                info["ended"] = datetime.now().isoformat()
                self.completed_tasks.append(info)
                if len(self.completed_tasks) > 20:
                    self.completed_tasks = self.completed_tasks[-20:]

    def get_status(self) -> dict:
        """Get full status for slide panel"""
        with self.lock:
            return {
                "timestamp": datetime.now().isoformat(),
                "active_agents": list(self.active_agents.values()),
                "active_agents_count": len(self.active_agents),
                "active_tasks": list(self.active_tasks.values()),
                "active_tasks_count": len(self.active_tasks),
                "completed_tasks": self.completed_tasks[-10:],  # last 10
                "completed_count": len(self.completed_tasks),
                "models_in_use": list(set([a["model"] for a in self.active_agents.values()] + [t["model"] for t in self.active_tasks.values() if t.get("model")]))
            }

    def get_for_tui(self) -> str:
        """Get formatted text for TUI panel"""
        status = self.get_status()
        lines = []
        lines.append(f"⏱️ {status['timestamp'][:19]} | Agents: {status['active_agents_count']} | Tasks: {status['active_tasks_count']}")
        lines.append("")

        if status["active_agents"]:
            lines.append("🤖 Active Agents:")
            for agent in status["active_agents"]:
                lines.append(f"  - {agent['name']} ({agent['model']})")
                lines.append(f"    Task: {agent['task'][:60]}")
                lines.append(f"    Status: {agent['status']} | Started: {agent['started'][11:19]} | {agent.get('progress','')}")
            lines.append("")

        if status["active_tasks"]:
            lines.append("📋 Active Tasks:")
            for task in status["active_tasks"]:
                lines.append(f"  - [{task['type']}] {task['description'][:60]}")
                lines.append(f"    Model: {task.get('model','')} | Agent: {task.get('agent','')} | {task['status']} | {task.get('progress','')}")
            lines.append("")

        if status["models_in_use"]:
            lines.append(f"🧠 Models in use: {', '.join(status['models_in_use'])}")
            lines.append("")

        if status["completed_tasks"]:
            lines.append("✅ Recently Completed (last 5):")
            for task in status["completed_tasks"][-5:]:
                name = task.get("name") or task.get("task_id") or task.get("description","")[:30]
                lines.append(f"  - {name} -> {task.get('status')} at {task.get('ended','')[11:19]}")

        if not status["active_agents"] and not status["active_tasks"]:
            lines.append("💤 No active agents or tasks - idle")
            lines.append("Try: 'Research Python async' or 'hermus subagent spawn ...' or 'hermus multiai debate ...'")

        return "\n".join(lines)

# Global tracker free
task_tracker = TaskTracker()
