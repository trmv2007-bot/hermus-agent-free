"""Mission, SWE Mode, Verification, Artifact, and Rollback Tools for Hermus Agent."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.artifact_manager import artifact_manager
from core.critic import critic_manager
from core.mission import mission_engine
from core.rollback import rollback_manager
from core.swe_mode import swe_mode
from core.verifier_registry import verifier_registry


def mission_start(
    goal: str,
    requirements: Optional[list[str]] = None,
    domain: Optional[str] = None,
    subgoals: Optional[list[str]] = None,
    budget_steps: int = 20,
) -> dict[str, Any]:
    """Start an objective-driven mission lifecycle with dynamic budgets and verification."""
    report = mission_engine.start_mission(
        goal=goal,
        requirements=requirements,
        domain=domain,
        subgoals=subgoals,
        budget_steps=budget_steps,
    )
    return report.to_dict()


def mission_resume(mission_id: str) -> dict[str, Any]:
    """Resume a paused, blocked, or interrupted mission."""
    report = mission_engine.resume_mission(mission_id)
    return report.to_dict()


def mission_status(mission_id: str) -> dict[str, Any]:
    """Get the current state, evidence, and progress of a mission."""
    report = mission_engine.get_mission(mission_id)
    if not report:
        return {"error": f"Mission '{mission_id}' not found"}
    return report.to_dict()


def swe_develop(
    task: str,
    workspace_dir: Optional[str] = None,
    max_repairs: int = 3,
) -> dict[str, Any]:
    """Execute dedicated Software Engineer mode: Inspect -> Plan -> Edit -> Build -> Test -> Repair -> Diff -> Package."""
    target = Path(workspace_dir) if workspace_dir else None
    res = swe_mode.execute(task=task, workspace_dir=target, max_repairs=max_repairs)
    return res.to_dict()


def domain_verify(
    domain: str = "auto",
    target_path: Optional[str] = None,
    task: Optional[str] = None,
    output: Optional[str] = None,
    port: Optional[int] = None,
) -> dict[str, Any]:
    """Run domain-specific verification for Python, Android, Web, Git, Linux, Research, or File tasks."""
    ctx: dict[str, Any] = {
        "task": task or "",
        "target_path": target_path,
        "file": target_path,
        "output": output or "",
        "port": port,
    }
    res = verifier_registry.verify(domain_or_auto=domain, context=ctx)
    return res.to_dict()


def artifact_list(
    mission_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
) -> dict[str, Any]:
    """List registered workspace artifacts (APKs, ZIPs, reports, diffs, builds)."""
    arts = artifact_manager.list_artifacts(mission_id=mission_id, artifact_type=artifact_type)
    return {
        "count": len(arts),
        "artifacts": [a.to_dict() for a in arts],
    }


def artifact_export(
    output_zip_path: str,
    mission_id: Optional[str] = None,
) -> dict[str, Any]:
    """Bundle artifacts into a standalone ZIP archive."""
    try:
        path = artifact_manager.export_bundle(output_zip_path=output_zip_path, mission_id=mission_id)
        return {"success": True, "bundle_path": path}
    except Exception as e:
        return {"success": False, "error": str(e)}


def rollback_checkpoint(
    label: str,
) -> dict[str, Any]:
    """Create a transactional checkpoint before risky changes."""
    cp = rollback_manager.checkpoint(label=label)
    return {"success": True, "checkpoint": cp.to_dict()}


def rollback_restore(
    checkpoint_id: str,
) -> dict[str, Any]:
    """Revert workspace to a previous checkpoint state."""
    return rollback_manager.restore(checkpoint_id=checkpoint_id)


def rollback_diff(
    checkpoint_id: str,
) -> dict[str, Any]:
    """Compare current workspace state with a checkpoint."""
    return rollback_manager.diff(checkpoint_id=checkpoint_id)


def critic_review(
    task: str,
    files: dict[str, str],
    execution_log: Optional[str] = None,
) -> dict[str, Any]:
    """Run independent critic panel: Code Review + Security Audit + Outcome Verification."""
    return critic_manager.run_full_review(
        task=task,
        files_content=files,
        execution_log=execution_log or "",
        requirements=[task],
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "mission_start",
            "description": "Start an objective-driven mission lifecycle with requirements, subgoals, dynamic budget, and domain verification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "The mission objective."},
                    "requirements": {"type": "array", "items": {"type": "string"}, "description": "Specific verifiable requirements."},
                    "domain": {"type": "string", "description": "Domain (python, android, web, git, linux, research, file)."},
                    "budget_steps": {"type": "integer", "description": "Dynamic step budget limit."},
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mission_resume",
            "description": "Resume a paused, blocked, or interrupted mission by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "description": "The mission ID."},
                },
                "required": ["mission_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "swe_develop",
            "description": "Run the dedicated Software Engineer mode lifecycle on a repository or project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The engineering task description."},
                    "workspace_dir": {"type": "string", "description": "Target workspace directory."},
                    "max_repairs": {"type": "integer", "description": "Maximum repair attempts."},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "domain_verify",
            "description": "Verify task outcome using specialized domain verifiers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain: python, android, web, git, linux, research, file, auto."},
                    "target_path": {"type": "string", "description": "Target file or directory path."},
                    "task": {"type": "string", "description": "Original task description."},
                    "output": {"type": "string", "description": "Command or execution output."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "artifact_list",
            "description": "List generated artifacts (APKs, reports, ZIPs, builds, diffs).",
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "description": "Filter by mission ID."},
                    "artifact_type": {"type": "string", "description": "Filter by artifact type."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rollback_checkpoint",
            "description": "Create a workspace checkpoint for safe transactional rollback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Human-readable checkpoint label."},
                },
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rollback_restore",
            "description": "Restore workspace files to a checkpoint state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "checkpoint_id": {"type": "string", "description": "Checkpoint ID to restore."},
                },
                "required": ["checkpoint_id"],
            },
        },
    },
]

TOOL_MAP = {
    "mission_start": mission_start,
    "mission_resume": mission_resume,
    "mission_status": mission_status,
    "swe_develop": swe_develop,
    "domain_verify": domain_verify,
    "artifact_list": artifact_list,
    "artifact_export": artifact_export,
    "rollback_checkpoint": rollback_checkpoint,
    "rollback_restore": rollback_restore,
    "rollback_diff": rollback_diff,
    "critic_review": critic_review,
}
