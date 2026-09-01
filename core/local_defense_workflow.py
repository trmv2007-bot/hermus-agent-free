"""Deterministic local-folder defensive scan mission workflow.

This gives Hermus one practical Jarvis-like mission path that does not require an
LLM to operate safely: pre-flight, approval bundle, approved read-only scan,
Markdown report artifact, and mission evidence.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

from .autonomy_preflight import preflight_goal
from .local_defense_scanner import scan_folder
from .mission import MissionReport, MissionState, mission_engine


def start_local_scan_mission(path: str, *, purpose: str = "malware", max_files: int = 500) -> MissionReport:
    goal = f"Local folder defensive scan: scan {path} for {purpose} indicators and save a report"
    mid = f"msn_scan_{int(time.time())}_{os.urandom(2).hex()}"
    preflight = preflight_goal(goal).to_dict()
    report = MissionReport(
        mission_id=mid,
        goal=goal,
        domain="local_defense",
        state=MissionState.BLOCKED.value,
        progress_pct=0,
        preflight=preflight,
        create_prompts_action={
            "label": "Create approval prompts for this local scan mission",
            "method": "POST",
            "endpoint": f"/missions/{mid}/preflight/approvals",
            "fallback_endpoint": "/safety/preflight/approvals",
            "payload": {"goal": goal},
            "cli": f"hermus safety preflight {goal!r} --create-approval-prompts",
        },
        blocker_reason="Local scan mission is waiting for scoped folder approval",
        blocker_instructions=(
            "Create/approve the suggested approval bundle, then run "
            f"POST /local-defense/missions/{mid}/run or `hermus safety scan-mission-run {mid}`."
        ),
        evidence=[{"stage": "local_defense_preflight", "status": preflight.get("status"), "path": path, "purpose": purpose, "max_files": max_files}],
        final_proof="MISSION BLOCKED: scoped local-folder scan approval required",
        recoverable=True,
    )
    # Store deterministic scan config in preflight metadata so execute can replay
    # the exact approved action later.
    report.preflight = dict(report.preflight or {})
    report.preflight["local_scan"] = {"path": path, "purpose": purpose, "max_files": int(max_files or 500)}
    mission_engine._save_mission(report)
    return report


def run_local_scan_mission(mission_id: str) -> MissionReport:
    report = mission_engine.get_mission(mission_id)
    if report is None:
        raise ValueError(f"Mission {mission_id} not found")
    config = ((report.preflight or {}).get("local_scan") or {}) if isinstance(report.preflight, dict) else {}
    path = str(config.get("path") or "")
    purpose = str(config.get("purpose") or "malware")
    max_files = int(config.get("max_files") or 500)
    if not path:
        raise ValueError(f"Mission {mission_id} has no local scan config")

    args = {"path": path, "purpose": purpose, "max_files": max_files, "save_report": True, "mission_id": mission_id}
    try:
        from .permissions import Decision, permission_manager

        check = permission_manager.check("local_folder_defensive_scan", args=args)
        if check.get("decision") != Decision.ALLOW.value:
            report.state = MissionState.BLOCKED.value
            report.blocker_reason = "Local scan mission still needs scoped approval"
            report.blocker_instructions = "Approve the pending local_folder_defensive_scan request/bundle, then run the scan mission again."
            report.approval_request = check.get("approval_request")
            report.evidence.append({"stage": "local_defense_permission", "status": "blocked", "permission": check})
            mission_engine._save_mission(report)
            return report
    except Exception as exc:  # noqa: BLE001
        report.state = MissionState.BLOCKED.value
        report.blocker_reason = f"Permission check failed closed: {exc}"
        mission_engine._save_mission(report)
        return report

    result = scan_folder(path, max_files=max_files, save_report=True, mission_id=mission_id)
    if not result.get("success"):
        report.state = MissionState.FAILED.value
        report.error = {"type": "local_defense_scan_failed", "stage": "local_defense_scan", "message": result.get("error")}
        report.final_proof = f"LOCAL DEFENSE SCAN FAILED: {result.get('error')}"
    else:
        report.state = MissionState.COMPLETED.value
        report.progress_pct = 100
        report.finished_at = datetime.now().isoformat()
        report.blocker_reason = None
        report.blocker_instructions = None
        report.approval_request = None
        art = result.get("report_artifact") or {}
        art_id = art.get("id") or result.get("report_path")
        if art_id and art_id not in report.artifacts:
            report.artifacts.append(art_id)
        report.evidence.append({
            "stage": "local_defense_scan",
            "status": "completed",
            "root": result.get("root"),
            "scanned_files": result.get("scanned_files"),
            "finding_count": result.get("finding_count"),
            "artifact": art_id,
        })
        report.final_proof = (
            f"LOCAL DEFENSE SCAN COMPLETE: scanned {result.get('scanned_files')} files, "
            f"found {result.get('finding_count')} suspicious indicators. Report: {result.get('report_path') or art_id}"
        )
        try:
            from .capability_registry import get_capability_registry

            get_capability_registry().register(
                "Local folder defensive scanner",
                category="private_data_scope",
                status="ready",
                source="local_defense_mission",
                notes="Approved scan mission completed successfully; broad/private scans still require scoped grants.",
            )
        except Exception:
            pass
    mission_engine._save_mission(report)
    return report


__all__ = ["run_local_scan_mission", "start_local_scan_mission"]
