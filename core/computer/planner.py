"""Goal-driven desktop planner that produces an executable visual state graph."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from ..llm import FreeLLM, free_llm
from .skills import ComputerSkill, ComputerSkillStore
from .world_state import WorldState


SUPPORTED_ACTIONS = {
    "click_target",
    "click",
    "double_click",
    "right_click",
    "type_text",
    "press_key",
    "hotkey",
    "scroll",
    "open_application",
    "close_application",
    "focus_window",
    "move_mouse",
    "wait_until",
}


@dataclass
class TaskGoal:
    objective: str
    success_condition: str
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanNode:
    name: str
    action: dict[str, Any]
    expected: str
    precondition: str = ""
    goal: str = ""
    on_success: Optional[str] = None
    on_failure: Optional[str] = None
    fallbacks: list[dict[str, Any]] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    agent: str = "computer-operator"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskGraph:
    task: str
    goal: TaskGoal
    nodes: list[PlanNode]
    start: Optional[str] = None
    success_terminal: str = "SUCCESS"
    failure_terminal: str = "FAILURE"
    source: str = "planner"
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.start is None and self.nodes:
            self.start = self.nodes[0].name

    def validate(self) -> dict[str, Any]:
        names = [node.name for node in self.nodes]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        known = set(names) | {self.success_terminal, self.failure_terminal}
        errors: list[str] = []
        if not self.nodes:
            errors.append("graph has no executable nodes")
        if duplicates:
            errors.append(f"duplicate node names: {', '.join(duplicates)}")
        if self.start and self.start not in set(names):
            errors.append(f"start node '{self.start}' does not exist")
        for node in self.nodes:
            kind = str(node.action.get("kind") or "") if isinstance(node.action, dict) else ""
            if kind not in SUPPORTED_ACTIONS:
                errors.append(f"node '{node.name}' uses unsupported action '{kind}'")
            if not node.expected:
                errors.append(f"node '{node.name}' has no expected visual state")
            for label, target in (("on_success", node.on_success), ("on_failure", node.on_failure)):
                if target and target not in known:
                    errors.append(f"node '{node.name}' {label} target '{target}' does not exist")
            unknown_dependencies = [item for item in node.depends_on if item not in set(names)]
            if unknown_dependencies:
                errors.append(f"node '{node.name}' has unknown dependencies: {unknown_dependencies}")

        # Detect cycles in success transitions. Failure transitions may
        # intentionally return to a recovery node and remain bounded by the
        # state machine's transition guard.
        transitions = {node.name: node.on_success for node in self.nodes if node.on_success in set(names)}
        for origin in names:
            seen: set[str] = set()
            cursor: Optional[str] = origin
            while cursor in transitions:
                if cursor in seen:
                    errors.append(f"success-transition cycle detected from '{origin}'")
                    break
                seen.add(cursor)
                cursor = transitions.get(cursor)
        return {"ok": not errors, "errors": list(dict.fromkeys(errors)), "warnings": self.warnings}

    def to_plan(self) -> list[dict[str, Any]]:
        return [node.to_dict() for node in self.nodes]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "goal": self.goal.to_dict(),
            "nodes": self.to_plan(),
            "start": self.start,
            "success_terminal": self.success_terminal,
            "failure_terminal": self.failure_terminal,
            "source": self.source,
            "warnings": self.warnings,
            "validation": self.validate(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskGraph":
        goal_data = data.get("goal") if isinstance(data.get("goal"), dict) else {}
        nodes = []
        for raw in data.get("nodes", data.get("plan", [])) or []:
            if not isinstance(raw, dict) or not isinstance(raw.get("action"), dict):
                continue
            nodes.append(PlanNode(
                name=str(raw.get("name") or f"STATE_{len(nodes)}"),
                action=dict(raw["action"]),
                expected=str(raw.get("expected") or ""),
                precondition=str(raw.get("precondition") or ""),
                goal=str(raw.get("goal") or ""),
                on_success=raw.get("on_success"),
                on_failure=raw.get("on_failure"),
                fallbacks=list(raw.get("fallbacks") or []),
                depends_on=list(raw.get("depends_on") or []),
                agent=str(raw.get("agent") or "computer-operator"),
                metadata=dict(raw.get("metadata") or {}),
            ))
        return cls(
            task=str(data.get("task") or ""),
            goal=TaskGoal(
                objective=str(goal_data.get("objective") or data.get("task") or ""),
                success_condition=str(goal_data.get("success_condition") or "The requested result is visibly confirmed"),
                constraints=[str(item) for item in goal_data.get("constraints", [])],
            ),
            nodes=nodes,
            start=data.get("start"),
            success_terminal=str(data.get("success_terminal") or "SUCCESS"),
            failure_terminal=str(data.get("failure_terminal") or "FAILURE"),
            source=str(data.get("source") or "persisted"),
            warnings=[str(item) for item in data.get("warnings", [])],
        )


class ComputerPlanner:
    """Convert a high-level request into a validated visual state graph."""

    def __init__(
        self,
        llm: Optional[FreeLLM] = None,
        skills: Optional[ComputerSkillStore] = None,
        world_state: Optional[WorldState] = None,
    ):
        self.llm = llm or free_llm
        self.skills = skills or ComputerSkillStore()
        self.world_state = world_state
        self.last_graph: Optional[TaskGraph] = None

    def plan(self, task: str) -> list[dict[str, Any]]:
        """Compatibility API returning planner step dictionaries."""
        return self.plan_graph(task).to_plan()

    def plan_graph(self, task: str, world_state: Optional[WorldState] = None) -> TaskGraph:
        world = world_state or self.world_state
        skill = self.skills.recall(task)
        if skill is not None and skill.procedure:
            graph = self._skill_graph(task, skill)
            if graph.validate()["ok"]:
                self.last_graph = graph
                return graph

        graph = self._decompose(task, world)
        validation = graph.validate()
        if not validation["ok"]:
            fallback = self._fallback_graph(task, world)
            fallback.warnings.extend(validation["errors"])
            graph = fallback
        self.last_graph = graph
        return graph

    @staticmethod
    def _normalize_skill_action(step: dict[str, Any]) -> Optional[dict[str, Any]]:
        action = step.get("action")
        if isinstance(action, str):
            action = {"kind": action, **(step.get("args") or {})}
        return dict(action) if isinstance(action, dict) else None

    def _skill_graph(self, task: str, skill: ComputerSkill) -> TaskGraph:
        nodes: list[PlanNode] = []
        procedure = [step for step in skill.procedure if isinstance(step, dict)]
        for index, step in enumerate(procedure):
            action = self._normalize_skill_action(step)
            if action is None:
                continue
            name = str(step.get("name") or f"SKILL_STATE_{index}")
            next_name = (
                str(procedure[index + 1].get("name") or f"SKILL_STATE_{index + 1}")
                if index + 1 < len(procedure) else "SUCCESS"
            )
            nodes.append(PlanNode(
                name=name,
                goal=str(step.get("goal") or f"Replay learned step for {task}"),
                precondition=str(step.get("precondition") or ""),
                action=action,
                expected=str(step.get("expected") or "The learned action has its intended visible effect"),
                on_success=str(step.get("on_success") or next_name),
                on_failure=step.get("on_failure"),
                metadata={
                    "recalled_from": skill.name,
                    "skill_success_rate": skill.success_rate,
                    "known_failures": list(skill.typical_failures),
                    "known_repairs": list(skill.repairs),
                },
            ))
        return TaskGraph(
            task=task,
            goal=TaskGoal(task, f"The learned procedure '{skill.name}' completes successfully"),
            nodes=nodes,
            source=f"skill:{skill.name}",
        )

    @staticmethod
    def _extract_json(content: str) -> Any:
        text = str(content or "").strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()
        decoder = json.JSONDecoder()
        starts = sorted(index for index in (text.find("{"), text.find("[")) if index >= 0)
        for start in starts:
            try:
                value, _ = decoder.raw_decode(text[start:])
                return value
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _sanitize_action(raw: Any) -> Optional[dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or raw.get("action") or "").strip()
        if kind not in SUPPORTED_ACTIONS:
            return None
        action = {"kind": kind}
        fields = {
            "click_target": ("target",),
            "click": ("x", "y"),
            "double_click": ("x", "y"),
            "right_click": ("x", "y"),
            "type_text": ("text",),
            "press_key": ("key",),
            "hotkey": ("keys",),
            "scroll": ("amount", "x", "y"),
            "open_application": ("name",),
            "close_application": ("name",),
            "focus_window": ("name",),
            "move_mouse": ("x", "y"),
            "wait_until": ("condition", "timeout"),
        }
        for field_name in fields[kind]:
            if field_name in raw:
                action[field_name] = raw[field_name]
        required = {
            "click_target": "target",
            "type_text": "text",
            "press_key": "key",
            "hotkey": "keys",
            "open_application": "name",
            "close_application": "name",
            "focus_window": "name",
            "wait_until": "condition",
        }
        required_field = required.get(kind)
        if required_field and not action.get(required_field):
            return None
        return action

    def _decompose(self, task: str, world: Optional[WorldState]) -> TaskGraph:
        world_json = json.dumps(world.to_dict(include_history=False), default=str) if world else "{}"
        prompt = f"""You are the Hermus desktop Task Planner. Turn the request into an executable visual state graph.

REQUEST: {task}
CURRENT WORLD STATE: {world_json}

Return ONLY JSON:
{{
  "goal": {{
    "objective": "what must be achieved",
    "success_condition": "final visible proof",
    "constraints": ["important safety or ordering constraint"]
  }},
  "nodes": [
    {{
      "name": "UNIQUE_UPPERCASE_STATE",
      "goal": "subgoal served by this action",
      "precondition": "optional visible condition required before action",
      "action": {{"kind": "supported kind", "target": "or other required args"}},
      "expected": "concrete visible state AFTER the action",
      "on_success": "NEXT_STATE or SUCCESS",
      "on_failure": "FAILURE or named fallback state",
      "fallbacks": [{{"cause": "known failure", "strategy": "safe response"}}],
      "depends_on": ["earlier state names"],
      "agent": "computer-operator"
    }}
  ]
}}

Supported actions: click_target, type_text, press_key, hotkey, scroll, open_application, close_application, focus_window, wait_until. Never invent coordinates. Decompose every clause. Include preconditions, visual postconditions, ordering dependencies, and explicit transitions. The final successful node must transition to SUCCESS. Unsafe or impossible requests should transition to FAILURE rather than guessing.
"""
        try:
            response = self.llm.chat([{"role": "user", "content": prompt}])
            parsed = self._extract_json(getattr(response, "content", str(response)))
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            parsed = {"nodes": parsed}
        if not isinstance(parsed, dict):
            return self._fallback_graph(task, world)

        raw_goal = parsed.get("goal") if isinstance(parsed.get("goal"), dict) else {}
        raw_nodes = parsed.get("nodes") if isinstance(parsed.get("nodes"), list) else []
        nodes: list[PlanNode] = []
        for index, raw in enumerate(raw_nodes):
            if not isinstance(raw, dict):
                continue
            action = self._sanitize_action(raw.get("action"))
            if action is None:
                continue
            nodes.append(PlanNode(
                name=re.sub(r"[^A-Za-z0-9_-]+", "_", str(raw.get("name") or f"STATE_{index}")).strip("_") or f"STATE_{index}",
                goal=str(raw.get("goal") or ""),
                precondition=str(raw.get("precondition") or ""),
                action=action,
                expected=str(raw.get("expected") or ""),
                on_success=str(raw["on_success"]) if raw.get("on_success") else None,
                on_failure=str(raw["on_failure"]) if raw.get("on_failure") else None,
                fallbacks=[item for item in raw.get("fallbacks", []) if isinstance(item, dict)],
                depends_on=[str(item) for item in raw.get("depends_on", [])],
                agent=str(raw.get("agent") or "computer-operator"),
            ))
        for index, node in enumerate(nodes):
            if not node.on_success:
                node.on_success = nodes[index + 1].name if index + 1 < len(nodes) else "SUCCESS"
        return TaskGraph(
            task=task,
            goal=TaskGoal(
                objective=str(raw_goal.get("objective") or task),
                success_condition=str(raw_goal.get("success_condition") or "The requested result is visibly confirmed"),
                constraints=[str(item) for item in raw_goal.get("constraints", [])],
            ),
            nodes=nodes,
            start=parsed.get("start"),
            source="llm",
        )

    @staticmethod
    def _step_name(prefix: str, index: int) -> str:
        return f"{prefix}_{index + 1}"

    def _fallback_graph(self, task: str, world: Optional[WorldState]) -> TaskGraph:
        """Safe deterministic decomposition; never clicks the entire request."""
        text = str(task or "").strip()
        lowered = text.lower()
        nodes: list[PlanNode] = []

        def add(prefix: str, action: dict[str, Any], expected: str, goal: str, precondition: str = "") -> None:
            name = self._step_name(prefix, len(nodes))
            if nodes:
                nodes[-1].on_success = name
            nodes.append(PlanNode(
                name=name,
                goal=goal,
                precondition=precondition,
                action=action,
                expected=expected,
                on_success="SUCCESS",
                on_failure=None,
            ))

        # Open/launch application.
        app_match = re.search(r"\b(?:open|launch|start)\s+([A-Za-z0-9 ._+-]+?)(?=\s+(?:and|then|to|,)|$)", text, re.I)
        if app_match:
            app = app_match.group(1).strip().strip(".")
            if app and app.lower() not in {"this", "that", "the file", "file", "program", "the program"}:
                add("OPEN_APP", {"kind": "open_application", "name": app},
                    f"The {app} application window is visible", f"Open {app}")

        # Browser navigation is intentionally decomposed into focus/type/enter.
        url_match = re.search(r"\b((?:https?://|www\.)[^\s,]+|[a-z0-9-]+\.(?:com|org|net|io|dev|app)(?:/[^\s,]*)?)", text, re.I)
        if url_match or re.search(r"\bgo to\s+([A-Za-z0-9._~:/?#@!$&'()*+,;=%-]+)", text, re.I):
            raw_url = (url_match.group(1) if url_match else re.search(r"\bgo to\s+([^\s,]+)", text, re.I).group(1)).rstrip(".")
            url = raw_url if re.match(r"^[a-z]+://", raw_url, re.I) else f"https://{raw_url}"
            host = urlparse(url).netloc or raw_url
            add("FOCUS_ADDRESS", {"kind": "hotkey", "keys": ["ctrl", "l"]},
                "The browser address bar is focused", "Focus the address bar")
            add("TYPE_URL", {"kind": "type_text", "text": url},
                f"The address bar contains {url}", f"Enter {url}")
            add("NAVIGATE", {"kind": "press_key", "key": "enter"},
                f"The {host} page is visibly loaded", f"Navigate to {host}")

        # Explicit click intent only; do not convert arbitrary prose to targets.
        for match in re.finditer(r"\bclick\s+(?:the\s+)?[\"']?([^,.;]+?)[\"']?(?=\s+(?:and|then)\b|[,.;]|$)", text, re.I):
            target = match.group(1).strip().strip("\"'")
            if target:
                add("CLICK_TARGET", {"kind": "click_target", "target": target},
                    f"Clicking {target} produces the intended visible result", f"Click {target}")

        type_match = re.search(r"\btype\s+[\"']([^\"']+)[\"']", text, re.I)
        if type_match:
            value = type_match.group(1)
            add("TYPE_TEXT", {"kind": "type_text", "text": value},
                f"The text {value} is visible in the focused field", "Enter the requested text")

        press_match = re.search(r"\bpress\s+(?:the\s+)?([A-Za-z0-9_+-]+)(?:\s+key)?", text, re.I)
        if press_match:
            key = press_match.group(1)
            add("PRESS_KEY", {"kind": "press_key", "key": key},
                f"Pressing {key} produces the intended visible result", f"Press {key}")

        if re.search(r"\bdownload\b", lowered):
            add("DOWNLOAD", {"kind": "click_target", "target": "Download button or link"},
                "The browser shows the download completed", "Download the requested file")
        if re.search(r"\b(?:unzip|extract)\b", lowered):
            add("OPEN_DOWNLOADS", {"kind": "open_application", "name": "File Manager"},
                "The file manager shows the downloaded archive", "Open the downloaded file location",
                "The browser shows the download completed")
            add("EXTRACT", {"kind": "click_target", "target": "Extract or Extract All"},
                "The extracted folder is visible", "Extract the downloaded archive",
                "The downloaded archive is visible")
        if re.search(r"\b(?:open|launch|start)\s+(?:the\s+)?program\b", lowered):
            add("LAUNCH_PROGRAM", {"kind": "click_target", "target": "The extracted program or executable"},
                "The program window is visible", "Launch the extracted program",
                "The extracted folder is visible")
        if re.search(r"\b(?:make sure|verify|test).*(?:works|working|success)", lowered):
            add("VERIFY_RESULT", {"kind": "wait_until", "condition": "The program is open and visibly responsive"},
                "The program is open and visibly responsive without an error dialog", "Verify the program works")

        warnings: list[str] = []
        if not nodes:
            # Safe fail-closed fallback: observe a concrete condition rather
            # than blindly clicking the entire natural-language request.
            warnings.append("No executable intent could be extracted without an LLM; using an observation-only state")
            add("OBSERVE_GOAL", {"kind": "wait_until", "condition": f"The screen visibly confirms: {text}"},
                f"The screen visibly confirms: {text}", "Determine whether the requested state is already complete")

        return TaskGraph(
            task=text,
            goal=TaskGoal(
                objective=text,
                success_condition=nodes[-1].expected,
                constraints=["Never invent coordinates", "Verify every visual transition"],
            ),
            nodes=nodes,
            source="deterministic_fallback",
            warnings=warnings,
        )
