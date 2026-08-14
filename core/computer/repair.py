"""Repair Engine — diagnose and recover from failed desktop actions."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..llm import FreeLLM, free_llm


class RepairEngine:
    """Diagnose visual verification failures and generate recovery steps."""

    def __init__(self, llm: Optional[FreeLLM] = None):
        self.llm = llm or free_llm

    def repair(
        self,
        failure_detail: str,
        expected: str,
        last_action: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate a short sequence of repair steps to recover from a failure."""
        action_desc = last_action.get("description") if last_action else "Unknown"
        prompt = f"""You are the Hermus Repair Agent. An autonomous desktop action failed.

LAST ACTION: {action_desc}
EXPECTED STATE: {expected}
DIAGNOSIS FROM VISION: {failure_detail}

Based on the vision diagnosis, determine if the failure was due to a popup, a misclick, the wrong application being open, or a timeout. Generate 1-3 repair steps to get back on track.

Supported action JSON format:
- {{"name": "REPAIR_STEP", "expected": "visual confirmation", "action": {{"kind": "click_target", "target": "button"}}}}
- {{"name": "REPAIR_STEP", "expected": "visual confirmation", "action": {{"kind": "press_key", "key": "escape"}}}}
- {{"name": "REPAIR_STEP", "expected": "visual confirmation", "action": {{"kind": "type_text", "text": "correction"}}}}

Respond ONLY with a JSON list of repair steps. No conversational text.
"""
        response = self.llm.chat([{"role": "user", "content": prompt}])
        content = response.content.strip()

        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            steps = json.loads(content)
            if isinstance(steps, list):
                return steps
        except (json.JSONDecodeError, TypeError):
            pass

        return []
