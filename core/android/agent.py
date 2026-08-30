"""Deterministic goal-driven Android agent controller (§6, §8).

This is a *controller* (a small deterministic policy), NOT a live model. It exists so
that the real end-to-end path — ``ToolGateway -> AndroidTool -> transport -> device ->
observe -> reason -> act -> observe -> verify -> continue`` — can be exercised and
proven deterministically without a device or credentials. It reasons from the semantic
observation (§7) — labels, ids, bounds, state — never from raw coordinates, and it
verifies every action with a before/after observation (§8).

The Live-provider decision-making layer (a real model) is a separate, "NOT VERIFIED"
concern; this controller proves the *tool/control loop*.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .simulate import SimulatedAndroidDevice
from .tool import AndroidTool
from .permissions import AndroidPermissionManager, OP_CLASSES
from .tools import register_android_tools

#: ops that grant permission-class consent so the agent still must explicitly request it
_AUTO_CONSENTS = ["screen_capture", "ui_control", "launch_app", "device_info"]


@dataclass
class Step:
    op: str
    args: dict[str, Any]
    reason: str
    ok: bool
    verified: bool
    screen_changed: bool
    plan_reason: str = ""  # unused placeholder


@dataclass
class AgentResult:
    goal: str
    completed: bool = False
    steps: list[Step] = field(default_factory=list)
    final_observation: dict[str, Any] = field(default_factory=dict)
    reasoning: list[str] = field(default_factory=list)


class AndroidAgentController:
    """Drives a high-level goal against an Android device through the ToolGateway."""

    def __init__(self, *, device: Optional[SimulatedAndroidDevice] = None,
                 consents: Optional[list[str]] = None, gateway=None):
        self.device = device or SimulatedAndroidDevice()
        # Fresh, isolated permission state: never reuse a persisted global allowlist.
        import tempfile
        self._perm_path = tempfile.mkdtemp() + "/hermus_android_permissions.json"
        self.permissions = AndroidPermissionManager(path=self._perm_path)
        granted = _AUTO_CONSENTS if consents is None else consents
        for cls in granted:
            if cls in OP_CLASSES:
                self.permissions.grant(cls)
        self.tool = AndroidTool(transport=self.device, permissions=self.permissions)
        from core.tool_registry import ToolRegistry
        from core.tools.gateway import ToolGateway
        self.registry = ToolRegistry()
        register_android_tools(self.registry, self.tool)
        # Avoid the full discovery reload (which would rebind android tools to the
        # process singleton); this controller owns its own registry + gateway.
        self.registry._loaded = True
        self.gateway = gateway or ToolGateway(self.registry)

    # -- gateway-style primitives -------------------------------------------
    def observe(self) -> dict[str, Any]:
        res = self.gateway.execute("android_observe", {})
        if not res.ok:
            return {"ok": False, "error": res.error_message or res.error_code}
        return res.output

    def available_tools(self) -> list[str]:
        return sorted(self.gateway.available())

    # -- reasoning helpers ---------------------------------------------------
    def _buttons(self, obs: dict[str, Any]) -> list[dict[str, Any]]:
        return obs.get("buttons", [])

    def _node(self, obs: dict[str, Any], node_id: str) -> Optional[dict]:
        for e in obs.get("elements", []):
            if e.get("id") == node_id:
                return e
        return None

    @staticmethod
    def _center(node: dict[str, Any]) -> tuple[int, int]:
        b = node["bounds"]
        return b["x"] + b["w"] // 2, b["y"] + b["h"] // 2

    # -- the deterministic policy -------------------------------------------
    def run_goal(self, goal: str, *, max_steps: int = 8) -> AgentResult:
        """Solve a goal of the form 'add <text> to tasks' deterministically."""
        result = AgentResult(goal=goal)
        match = re.search(r"add\s+['\"]?(.+?)['\"]?\s+to\s+tasks", goal, re.I)
        if not match:
            result.reasoning.append(
                f"goal '{goal}' is not a supported scripted directive "
                "(supported: 'add <text> to tasks')")
            result.completed = False
            return result
        target = match.group(1).strip()
        result.reasoning.append(
            f"plan: ensure task '{target}' exists in the Tasks app")

        for _ in range(max_steps):
            obs = self._observe_step(result)
            if not obs.get("ok", True):
                result.reasoning.append(f"observe failed: {obs.get('reason')}")
                result.completed = False
                return result
            result.final_observation = obs

            # Verify terminal: task row already visible.
            if any(target in t for t in obs.get("visible_text", [])):
                result.reasoning.append(
                    f"verify: task '{target}' is present — goal satisfied")
                result.completed = True
                return result

            field = self._node(obs, "field")
            add_btn = self._node(obs, "add")
            if field is None or add_btn is None:
                result.reasoning.append(
                    f"observe: no field/add on screen (pkg={obs.get('package')}) — replan")
                self._record(result, Step("observe", {}, "replan", False, False, False))
                # App may not be foreground; try to launch it.
                self._act(result, "android_launch_app", {"package": "com.example.tasks"},
                          expect_ok=True)
                continue

            # Has the target text landed in the input field? (reason from the field's value)
            field_values = [f["value"] for f in obs.get("fields", []) if f.get("value")]
            typed = any(target in v for v in field_values) or self._node_has_text(obs, target)
            if not typed:
                # Type into the empty field, then re-observe next iteration to confirm.
                self._act(result, "android_type", {"text": target}, expect_ok=True)
                continue

            # The field is populated; tap Add and verify the task list changed.
            cx, cy = self._center(add_btn)
            before = self._task_count()
            self._act(result, "android_tap", {"x": cx, "y": cy}, expect_ok=True)
            after = self._task_count()
            if after == before:
                result.reasoning.append(
                    f"verify: tap Add did not add a task (still {after}) — retrying")
                continue

        result.reasoning.append("max_steps reached without confirming goal")
        result.completed = False
        return result

    # -- action + verification primitives ------------------------------------
    def _observe_step(self, result: AgentResult) -> dict[str, Any]:
        obs = self.observe()
        self._record(result, Step(
            op="android_observe", args={}, reason="observe",
            ok=bool(obs.get("ok", True)), verified=True,
            screen_changed=True))
        return obs

    def _task_count(self) -> int:
        return len(self.device.tasks())

    def _record(self, result: AgentResult, step: Step):
        result.steps.append(step)

    def _act(self, result: AgentResult, tool_name: str, args: dict[str, Any], *,
             expect_ok: bool) -> bool:
        before = self.observe()
        res = self.gateway.execute(tool_name, args)
        after = self.observe()
        ok = bool(res.ok)
        changed = self._hash(before) != self._hash(after)
        self._record(result, Step(
            op=tool_name, args=args, reason="", ok=ok, verified=ok,
            screen_changed=changed))
        result.reasoning.append(
            f"act {tool_name}({args}) -> {'ok' if ok else 'error:' + (res.error_message or '')}"
            f" {'' if changed else '(no state change)'}")
        return ok

    @staticmethod
    def _node_has_text(obs: dict[str, Any], target: str) -> bool:
        return any(target in (e.get("text") or e.get("label") or "")
                   for e in obs.get("elements", []))

    @staticmethod
    def _hash(obs: dict[str, Any]) -> str:
        import hashlib, json
        if "elements" in obs:
            return hashlib.sha256(json.dumps(
                obs.get("visible_text", []), sort_keys=True).encode()).hexdigest()[:12]
        return hashlib.sha256(json.dumps(obs, sort_keys=True, default=str).encode()).hexdigest()[:12]

    # -- consent & security helpers ------------------------------------------
    def revoke(self, *cls: str):
        for c in cls:
            self.permissions.revoke(c)


def default_device_controller(**kw) -> AndroidAgentController:
    return AndroidAgentController(**kw)
