"""Task Planner v2 — decompose high-level desktop tasks into multi-step plans."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Callable

from ..llm import FreeLLM, free_llm
from .skills import ComputerSkillStore


class ComputerPlanner:
    """Decompose natural-language tasks into executable visual state sequences."""

    def __init__(
        self,
        llm: Optional[FreeLLM] = None,
        skills: Optional[ComputerSkillStore] = None,
    ):
        self.llm = llm or free_llm
        self.skills = skills or ComputerSkillStore()

    def plan(self, task: str) -> List[Dict[str, Any]]:
        """Turn a task into a list of {name, expected, action} steps."""
        # 1. Try to recall an existing skill first.
        skill = self.skills.recall(task)
        if skill is not None and skill.procedure:
            plan = []
            for step in skill.procedure:
                plan.append({
                    "name": step.get("name", "step"),
                    "expected": step.get("expected", ""),
                    "action": step.get("action"),
                    "_recalled_from": skill.name,
                })
            return plan

        # 2. Fall back to LLM-driven decomposition.
        return self._decompose(task)

    def _decompose(self, task: str) -> List[Dict[str, Any]]:
        """Use an LLM to break a new task into visual-state machine steps."""
        prompt = f"""You are the Hermus Task Planner. Your job is to decompose a high-level desktop computer task into a sequence of executable visual states.

A task is a list of steps. Each step MUST have:
1. "name": A short uppercase name for the state (e.g., "OPEN_BROWSER").
2. "expected": A clear visual description of what the screen should look like when in this state (e.g., "A web browser window is visible with an address bar").
3. "action": The command to advance FROM this state. Supported actions:
   - {{"kind": "click_target", "target": "text or button label"}}
   - {{"kind": "type_text", "text": "text to type"}}
   - {{"kind": "press_key", "key": "enter"}}
   - {{"kind": "hotkey", "keys": ["control", "c"]}}
   - {{"kind": "open_application", "name": "app name"}}
   - {{"kind": "wait_until", "condition": "visual description"}}

Task: "{task}"

Respond ONLY with a valid JSON list of step objects. No conversational text.

Example for "Install Firefox":
[
  {{
    "name": "START",
    "expected": "The desktop is visible",
    "action": {{"kind": "open_application", "name": "terminal"}}
  }},
  {{
    "name": "TERMINAL_OPEN",
    "expected": "A terminal window is open",
    "action": {{"kind": "type_text", "text": "sudo apt install firefox-esr -y"}}
  }},
  {{
    "name": "COMMAND_TYPED",
    "expected": "The installation command is in the terminal",
    "action": {{"kind": "press_key", "key": "enter"}}
  }},
  {{
    "name": "INSTALLING",
    "expected": "Installation progress is visible in the terminal",
    "action": {{"kind": "wait_until", "condition": "The terminal shows the installation finished"}}
  }}
]
"""
        response = self.llm.chat([{"role": "user", "content": prompt}])
        content = response.content.strip()
        
        # Robust JSON extraction
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        try:
            plan = json.loads(content)
            if isinstance(plan, list):
                # Basic validation/cleanup
                sanitized = []
                for step in plan:
                    if isinstance(step, dict) and "action" in step:
                        sanitized.append({
                            "name": str(step.get("name", "STEP")),
                            "expected": str(step.get("expected", "")),
                            "action": step.get("action"),
                        })
                return sanitized
        except (json.JSONDecodeError, TypeError):
            pass

        # Ultimate fallback: single-step vision action
        return [{
            "name": "ACT",
            "expected": "",
            "action": {"kind": "click_target", "target": task},
        }]
