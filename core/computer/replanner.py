"""Adaptive Replanner - dynamically updates task graphs when reality diverges from expectations.

The replanner enables the agent to modify its plan mid-execution when:
1. Expected UI state doesn't match observed state
2. A step fails in an unexpected way
3. New opportunities or obstacles appear

This is NOT the same as repair - repair fixes a failed action within the current plan,
while replanning modifies the plan structure itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .planner import PlanNode, TaskGraph
from .world_state import WorldState


class ReplanReason(str, Enum):
    """Why replanning was triggered."""
    EXPECTED_STATE_MISMATCH = "expected_state_mismatch"
    STEP_FAILED_UNEXPECTEDLY = "step_failed_unexpectedly"
    NEW_OPPORTUNITY = "new_opportunity"
    OBSTACLE_APPEARED = "obstacle_appeared"
    PLAN_EXHAUSTED = "plan_exhausted"
    USER_REQUEST = "user_request"


class ReplanStrategy(str, Enum):
    """How to modify the plan."""
    REPLACE_CURRENT_STEP = "replace_current_step"
    INSERT_STEPS_BEFORE = "insert_steps_before"
    INSERT_STEPS_AFTER = "insert_steps_after"
    SKIP_CURRENT_STEP = "skip_current_step"
    REORDER_REMAINING = "reorder_remaining"
    FULL_REPLAN = "full_replan"


@dataclass
class PlanDelta:
    """A modification to the task graph."""
    strategy: ReplanStrategy
    reason: ReplanReason
    affected_state: str
    new_nodes: list[PlanNode] = field(default_factory=list)
    nodes_to_remove: list[str] = field(default_factory=list)
    nodes_to_reorder: list[str] = field(default_factory=list)
    confidence: float = 0.0
    explanation: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "reason": self.reason.value,
            "affected_state": self.affected_state,
            "new_nodes": [n.to_dict() for n in self.new_nodes],
            "nodes_to_remove": list(self.nodes_to_remove),
            "nodes_to_reorder": list(self.nodes_to_reorder),
            "confidence": round(self.confidence, 4),
            "explanation": self.explanation,
            "evidence": dict(self.evidence),
        }


@dataclass
class ReplanContext:
    """Context for a replanning decision."""
    original_task: str
    current_state: str
    expected_state: str
    observed_state: dict[str, Any]
    world_state: WorldState
    plan_so_far: list[dict[str, Any]]
    remaining_plan: list[dict[str, Any]]
    failure_reason: Optional[str] = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "original_task": self.original_task,
            "current_state": self.current_state,
            "expected_state": self.expected_state,
            "observed_state": self.observed_state,
            "plan_so_far": list(self.plan_so_far),
            "remaining_plan": list(self.remaining_plan),
            "failure_reason": self.failure_reason,
        }


class AdaptiveReplanner:
    """Modifies task graphs when execution diverges from expectations.
    
    The replanner is invoked when:
    1. A verification fails after repair attempts
    2. The world state doesn't match expected conditions
    3. The planner detects a fundamental assumption is wrong
    
    It does NOT replace the repair system - repair handles action-level failures,
    while replanning handles plan-level corrections.
    """
    
    def __init__(
        self,
        llm: Optional[Any] = None,
        max_inserted_steps: int = 5,
        replan_threshold: float = 0.3,
    ):
        self.llm = llm
        self.max_inserted_steps = max_inserted_steps
        self.replan_threshold = replan_threshold  # Confidence below which to replan
        self.replan_history: list[PlanDelta] = []
    
    def should_replan(
        self,
        current_state: str,
        expected_state: str,
        verification_confidence: float,
        failure_kind: str,
    ) -> bool:
        """Determine if we should trigger adaptive replanning.
        
        Args:
            current_state: The state that failed
            expected_state: What we expected to see
            verification_confidence: How confident we are in the verification
            failure_kind: The type of failure that occurred
        
        Returns:
            True if we should replan instead of just retrying
        """
        # Don't replan for simple failures
        if failure_kind in ("target_not_found", "misclick", "timeout"):
            return verification_confidence < self.replan_threshold
        
        # Always consider replanning for these
        if failure_kind in ("blocking_dialog", "wrong_window", "input_focus"):
            return True
        
        # Replan if verification is very uncertain
        if verification_confidence < 0.2:
            return True
        
        return False
    
    def analyze_mismatch(
        self,
        expected: str,
        observed: dict[str, Any],
        world_state: WorldState,
    ) -> dict[str, Any]:
        """Analyze why expected state doesn't match observed state.
        
        Returns structured analysis of:
        - What's different
        - What might have caused it
        - What might be needed to correct it
        """
        analysis = {
            "mismatch_detected": True,
            "expected": expected,
            "observed": observed,
            "differences": [],
            "probable_causes": [],
            "suggested_corrections": [],
            "confidence": 0.0,
        }
        
        # Get current world state as text
        current_app = world_state.active_application or "unknown"
        current_window = world_state.active_window or "unknown"
        visible = world_state.visible_targets or []
        dialogs = world_state.dialogs or []
        
        # Analyze differences
        if expected.lower() not in " ".join(visible).lower():
            analysis["differences"].append("expected_target_not_visible")
            analysis["probable_causes"].append("target_moved_or_changed")
            analysis["suggested_corrections"].append("find_updated_target_location")
        
        if dialogs:
            analysis["differences"].append("unexpected_dialog_present")
            analysis["probable_causes"].append("dialog_blocking_action")
            analysis["suggested_corrections"].append("dismiss_dialog_first")
        
        if "window" in expected.lower() and world_state.active_window:
            # Window mismatch
            expected_window = self._extract_window_name(expected)
            if expected_window and expected_window.lower() != current_window.lower():
                analysis["differences"].append("wrong_window_active")
                analysis["probable_causes"].append("window_switched")
                analysis["suggested_corrections"].append("switch_to_correct_window")
        
        # Calculate confidence in analysis
        analysis["confidence"] = 0.7 if analysis["differences"] else 0.3
        
        return analysis
    
    def _extract_window_name(self, text: str) -> Optional[str]:
        """Extract window/application name from text."""
        import re
        match = re.search(r"(?:in|on|for)\s+([A-Za-z][A-Za-z0-9 ._-]{2,40})", text)
        if match:
            return match.group(1).strip()
        match = re.search(r"([A-Za-z][A-Za-z0-9 ._-]{3,40})\s+window", text, re.I)
        if match:
            return match.group(1).strip()
        return None
    
    def create_delta(
        self,
        context: ReplanContext,
        analysis: dict[str, Any],
    ) -> PlanDelta:
        """Create a plan modification based on the analysis.
        
        Args:
            context: Current replanning context
            analysis: Result from analyze_mismatch
        
        Returns:
            PlanDelta describing the modification
        """
        differences = analysis.get("differences", [])
        corrections = analysis.get("suggested_corrections", [])
        
        # Determine strategy based on what we found
        if "unexpected_dialog_present" in differences:
            # Insert dialog dismissal before current step
            return self._delta_for_dialog(context, corrections)
        
        elif "wrong_window_active" in differences:
            # Insert window switch before current step
            return self._delta_for_window_switch(context, analysis)
        
        elif "expected_target_not_visible" in differences:
            # Replace current step with updated target finding
            return self._delta_for_target_update(context, analysis)
        
        else:
            # Generic insertion of observation step
            return self._delta_for_generic_insert(context, analysis)
    
    def _delta_for_dialog(
        self,
        context: ReplanContext,
        corrections: list[str],
    ) -> PlanDelta:
        """Create delta for handling unexpected dialog."""
        # Find appropriate dismiss action
        dismiss_action = {
            "kind": "press_key",
            "key": "escape",
        }
        
        wait_action = {
            "kind": "wait_until",
            "condition": "The unexpected dialog or popup is no longer visible",
        }
        
        nodes = [
            PlanNode(
                name="DISMISS_INTERSTITIAL",
                goal="Dismiss the blocking dialog or popup",
                action=dismiss_action,
                expected="The blocking dialog is no longer visible",
                on_success=context.current_state,
                metadata={"replan_generated": True},
            ),
            PlanNode(
                name="VERIFY_DISMISSED",
                goal="Confirm the dialog was dismissed",
                action=wait_action,
                expected="The dialog is gone and the original action can proceed",
                on_success=context.current_state,
                metadata={"replan_generated": True},
            ),
        ]
        
        return PlanDelta(
            strategy=ReplanStrategy.INSERT_STEPS_BEFORE,
            reason=ReplanReason.OBSTACLE_APPEARED,
            affected_state=context.current_state,
            new_nodes=nodes,
            confidence=0.85,
            explanation="An unexpected dialog was blocking the action. Inserting dismissal steps before retrying.",
            evidence={"dialog_present": True},
        )
    
    def _delta_for_window_switch(
        self,
        context: ReplanContext,
        analysis: dict[str, Any],
    ) -> PlanDelta:
        """Create delta for wrong window."""
        window_name = self._extract_window_name(context.expected_state) or "the correct window"
        
        focus_action = {
            "kind": "focus_window",
            "name": window_name,
        }
        
        wait_action = {
            "kind": "wait_until",
            "condition": f"The {window_name} window is active and visible",
        }
        
        nodes = [
            PlanNode(
                name="SWITCH_TO_INTENDED_WINDOW",
                goal=f"Focus the {window_name} window",
                action=focus_action,
                expected=f"The {window_name} window is now active",
                on_success=context.current_state,
                metadata={"replan_generated": True, "target_window": window_name},
            ),
            PlanNode(
                name="VERIFY_WINDOW_FOCUSED",
                goal="Confirm the correct window is active",
                action=wait_action,
                expected=f"The {window_name} window is visible and responsive",
                on_success=context.current_state,
                metadata={"replan_generated": True},
            ),
        ]
        
        return PlanDelta(
            strategy=ReplanStrategy.INSERT_STEPS_BEFORE,
            reason=ReplanReason.OBSTACLE_APPEARED,
            affected_state=context.current_state,
            new_nodes=nodes,
            confidence=0.80,
            explanation=f"The wrong window was active. Switching to {window_name} before continuing.",
            evidence={"wrong_window": True, "target_window": window_name},
        )
    
    def _delta_for_target_update(
        self,
        context: ReplanContext,
        analysis: dict[str, Any],
    ) -> PlanDelta:
        """Create delta for when expected target isn't visible."""
        # This generates a step that re-observes and re-targets
        observe_action = {
            "kind": "wait_until",
            "condition": "Observe the current screen to find the updated target location",
        }
        
        # We'll replace the current step with an updated version
        nodes = [
            PlanNode(
                name="REFRESH_TARGET_OBSERVATION",
                goal="Re-observe the screen to find the target in its current location",
                action=observe_action,
                expected="The target is visible and its location has been updated",
                on_success=context.current_state,
                metadata={
                    "replan_generated": True,
                    "replaces": context.current_state,
                },
            ),
        ]
        
        return PlanDelta(
            strategy=ReplanStrategy.REPLACE_CURRENT_STEP,
            reason=ReplanReason.EXPECTED_STATE_MISMATCH,
            affected_state=context.current_state,
            new_nodes=nodes,
            nodes_to_remove=[context.current_state],
            confidence=0.75,
            explanation="The expected target was not found. Inserting a re-observation step to find it.",
            evidence={"target_not_found": True},
        )
    
    def _delta_for_generic_insert(
        self,
        context: ReplanContext,
        analysis: dict[str, Any],
    ) -> PlanDelta:
        """Create a generic observation insert."""
        nodes = [
            PlanNode(
                name="ADAPTIVE_OBSERVE",
                goal="Re-assess the current state before continuing",
                action={"kind": "wait_until", "condition": "Reassess the current screen state"},
                expected="The current state has been re-evaluated",
                on_success=context.current_state,
                metadata={"replan_generated": True},
            ),
        ]
        
        return PlanDelta(
            strategy=ReplanStrategy.INSERT_STEPS_BEFORE,
            reason=ReplanReason.EXPECTED_STATE_MISMATCH,
            affected_state=context.current_state,
            new_nodes=nodes,
            confidence=0.60,
            explanation="State mismatch detected. Inserting observation step to re-evaluate.",
            evidence=analysis,
        )
    
    def apply_delta(
        self,
        graph: TaskGraph,
        delta: PlanDelta,
    ) -> TaskGraph:
        """Apply a PlanDelta to a TaskGraph.
        
        Args:
            graph: The current task graph
            delta: The modification to apply
        
        Returns:
            Modified TaskGraph with the delta applied
        """
        if delta.strategy == ReplanStrategy.REPLACE_CURRENT_STEP:
            return self._apply_replace(graph, delta)
        elif delta.strategy == ReplanStrategy.INSERT_STEPS_BEFORE:
            return self._apply_insert_before(graph, delta)
        elif delta.strategy == ReplanStrategy.INSERT_STEPS_AFTER:
            return self._apply_insert_after(graph, delta)
        elif delta.strategy == ReplanStrategy.SKIP_CURRENT_STEP:
            return self._apply_skip(graph, delta)
        else:
            # Default to insert before
            return self._apply_insert_before(graph, delta)
    
    def _apply_replace(
        self,
        graph: TaskGraph,
        delta: PlanDelta,
    ) -> TaskGraph:
        """Replace a node and its successors."""
        new_nodes = []
        for node in graph.nodes:
            if node.name == delta.affected_state:
                # Insert new nodes
                for new_node in delta.new_nodes:
                    new_nodes.append(new_node)
                # Skip removed nodes
                if delta.nodes_to_remove:
                    continue
            else:
                new_nodes.append(node)
        
        return TaskGraph(
            task=graph.task,
            goal=graph.goal,
            nodes=new_nodes,
            start=graph.start,
            success_terminal=graph.success_terminal,
            failure_terminal=graph.failure_terminal,
            source="adaptive_replan",
            warnings=[f"Adapted at {delta.affected_state}: {delta.explanation}"],
        )
    
    def _apply_insert_before(
        self,
        graph: TaskGraph,
        delta: PlanDelta,
    ) -> TaskGraph:
        """Insert new nodes before the affected state."""
        new_nodes = []
        inserted = False
        
        for node in graph.nodes:
            if node.name == delta.affected_state and not inserted:
                # Insert new nodes first
                for i, new_node in enumerate(delta.new_nodes):
                    # Wire up on_success
                    if i == len(delta.new_nodes) - 1:
                        new_node.on_success = node.name
                    else:
                        new_node.on_success = delta.new_nodes[i + 1].name
                    new_nodes.append(new_node)
                inserted = True
            
            # Update predecessor's on_success if it pointed to affected state
            if new_nodes and new_nodes[-1].name != delta.affected_state:
                if new_nodes[-1].on_success == delta.affected_state:
                    new_nodes[-1].on_success = delta.new_nodes[0].name
            
            new_nodes.append(node)
        
        # If affected state wasn't found, append new nodes at end
        if not inserted:
            for new_node in delta.new_nodes:
                new_node.on_success = graph.success_terminal
                new_nodes.append(new_node)
        
        return TaskGraph(
            task=graph.task,
            goal=graph.goal,
            nodes=new_nodes,
            start=graph.start,
            success_terminal=graph.success_terminal,
            failure_terminal=graph.failure_terminal,
            source="adaptive_replan",
            warnings=[f"Inserted {len(delta.new_nodes)} steps before {delta.affected_state}"],
        )
    
    def _apply_insert_after(
        self,
        graph: TaskGraph,
        delta: PlanDelta,
    ) -> TaskGraph:
        """Insert new nodes after the affected state."""
        new_nodes = []
        
        for node in graph.nodes:
            new_nodes.append(node)
            
            if node.name == delta.affected_state:
                # Insert new nodes after this one
                next_name = node.on_success
                for i, new_node in enumerate(delta.new_nodes):
                    new_node.on_success = next_name if i == len(delta.new_nodes) - 1 else delta.new_nodes[i + 1].name
                    next_name = new_node.name
                    new_nodes.append(new_node)
        
        return TaskGraph(
            task=graph.task,
            goal=graph.goal,
            nodes=new_nodes,
            start=graph.start,
            success_terminal=graph.success_terminal,
            failure_terminal=graph.failure_terminal,
            source="adaptive_replan",
            warnings=[f"Inserted {len(delta.new_nodes)} steps after {delta.affected_state}"],
        )
    
    def _apply_skip(
        self,
        graph: TaskGraph,
        delta: PlanDelta,
    ) -> TaskGraph:
        """Skip the affected state."""
        new_nodes = []
        
        for node in graph.nodes:
            if node.name == delta.affected_state:
                # Wire previous node to next
                if new_nodes:
                    new_nodes[-1].on_success = node.on_success
                continue
            new_nodes.append(node)
        
        return TaskGraph(
            task=graph.task,
            goal=graph.goal,
            nodes=new_nodes,
            start=graph.start,
            success_terminal=graph.success_terminal,
            failure_terminal=graph.failure_terminal,
            source="adaptive_replan",
            warnings=[f"Skipped {delta.affected_state}"],
        )
    
    def replan(
        self,
        context: ReplanContext,
        max_deltas: int = 3,
    ) -> tuple[Optional[TaskGraph], list[PlanDelta]]:
        """Perform adaptive replanning.
        
        Args:
            context: Current execution context
            max_deltas: Maximum number of deltas to apply
        
        Returns:
            Tuple of (modified graph or None, list of deltas applied)
        """
        deltas = []
        analysis = self.analyze_mismatch(
            context.expected_state,
            context.observed_state,
            context.world_state,
        )
        
        if not analysis.get("differences"):
            return None, []
        
        # Create initial delta
        delta = self.create_delta(context, analysis)
        deltas.append(delta)
        self.replan_history.append(delta)
        
        # Try to reconstruct original graph from context
        all_steps = list(context.plan_so_far) + list(context.remaining_plan)
        if not all_steps:
            return None, deltas
        
        # Build a TaskGraph from the steps
        from .planner import PlanNode, TaskGoal
        
        nodes = []
        for step in all_steps:
            if isinstance(step, dict):
                action = step.get("action", {})
                if isinstance(action, str):
                    action = {"kind": action}
                nodes.append(PlanNode(
                    name=str(step.get("name", "STATE")),
                    action=action,
                    expected=str(step.get("expected", "")),
                    goal=str(step.get("goal", "")),
                    on_success=step.get("on_success"),
                ))
        
        # Add terminal nodes
        node_names = [n.name for n in nodes]
        if "SUCCESS" not in node_names:
            nodes.append(PlanNode(name="SUCCESS", action=None, expected="", on_success=None, terminal=True))
        
        graph = TaskGraph(
            task=context.original_task,
            goal=TaskGoal(context.original_task, "Task completed successfully"),
            nodes=nodes,
            source="reconstructed",
        )
        
        # Apply deltas
        for delta in deltas[:max_deltas]:
            graph = self.apply_delta(graph, delta)
        
        return graph, deltas
    
    def get_replan_history(self) -> list[dict[str, Any]]:
        """Get history of replanning decisions."""
        return [d.to_dict() for d in self.replan_history]
    
    def can_replan(self, replan_count: int, max_replans: int = 3) -> bool:
        """Check if we're allowed to replan (prevent infinite loops)."""
        return replan_count < max_replans


# Convenience function
def create_replanner(**kwargs) -> AdaptiveReplanner:
    return AdaptiveReplanner(**kwargs)
