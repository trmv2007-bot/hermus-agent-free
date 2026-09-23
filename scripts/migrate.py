"""Migration tooling for Fleet v2 adoption."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import config
from core.log import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Data paths
# --------------------------------------------------------------------------- #

class MigrationPaths:
    """All data paths that may need migration."""

    def __init__(self, base_dir: Path | None = None):
        self.base = base_dir or config.base_dir
        self.data = self.base / "data"

        # Legacy agent data (pre-Fleet v2)
        self.legacy_agents = self.data / "agents"  # may not exist
        self.legacy_harness = self.data / "harness"
        self.legacy_subagents = self.data / "subagents"  # may not exist

        # Fleet v2 data
        self.fleet = self.data / "fleet"
        self.fleet_agents = self.fleet / "agents"
        self.fleet_bus = self.fleet / "bus-*.jsonl"

        # Other user data that should be preserved
        self.memory_db = self.data / "memory.db"
        self.memory2_db = self.data / "memory2.db"
        self.embeddings_db = self.data / "embeddings.db"
        self.api_keys = self.data / "api_keys.json"
        self.custom_apis = self.data / "custom_apis.json"
        self.presence = self.data / "presence.json"
        self.missions = self.data / "missions"
        self.logs = self.data / "logs"
        self.sessions = self.data / "sessions"
        self.trajectories = self.data / "trajectories.jsonl"
        self.self_improvement = self.data / "self_improvement.json"

    def all_user_data_paths(self) -> list[Path]:
        """Return all user data paths that should be preserved."""
        return [
            self.memory_db,
            self.memory2_db,
            self.embeddings_db,
            self.api_keys,
            self.custom_apis,
            self.presence,
            self.missions,
            self.logs,
            self.sessions,
            self.trajectories,
            self.self_improvement,
        ]

    def legacy_paths(self) -> list[Path]:
        """Return legacy paths that may need migration."""
        return [
            self.legacy_agents,
            self.legacy_harness,
            self.legacy_subagents,
        ]

    def fleet_v2_paths(self) -> list[Path]:
        """Return Fleet v2 paths that are the migration targets."""
        return [
            self.fleet,
            self.fleet_agents,
        ]


# --------------------------------------------------------------------------- #
# Migration report
# --------------------------------------------------------------------------- #

class MigrationReport:
    """Accumulates migration steps and produces a report."""

    def __init__(self):
        self.steps: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.started_at = datetime.now().isoformat()

    def add_step(self, phase: str, action: str, source: Path | None = None, target: Path | None = None, details: dict | None = None):
        self.steps.append({
            "phase": phase,
            "action": action,
            "source": str(source) if source else None,
            "target": str(target) if target else None,
            "details": details or {},
            "timestamp": datetime.now().isoformat(),
        })

    def add_warning(self, msg: str):
        self.warnings.append(msg)
        logger.warning("[migration] %s", msg)

    def add_error(self, msg: str):
        self.errors.append(msg)
        logger.error("[migration] %s", msg)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "completed_at": datetime.now().isoformat(),
            "steps": self.steps,
            "warnings": self.warnings,
            "errors": self.errors,
            "summary": {
                "total_steps": len(self.steps),
                "warnings": len(self.warnings),
                "errors": len(self.errors),
            }
        }

    def save(self, path: Path):
        path.write_text(json.dumps(self.to_dict(), indent=2))


# --------------------------------------------------------------------------- #
# Migration operations
# --------------------------------------------------------------------------- #

def inspect(paths: MigrationPaths, report: MigrationReport) -> dict[str, Any]:
    """Inspect existing data and report what would be migrated."""
    result = {
        "legacy_data": {},
        "fleet_v2_data": {},
        "user_data": {},
        "recommendations": [],
    }

    # Check legacy agent data
    if paths.legacy_agents.exists():
        agent_files = list(paths.legacy_agents.glob("*.json"))
        result["legacy_data"]["agents"] = {
            "path": str(paths.legacy_agents),
            "count": len(agent_files),
            "files": [f.name for f in agent_files],
        }
        report.add_step("inspect", "found_legacy_agents", source=paths.legacy_agents, details={"count": len(agent_files)})
    else:
        result["legacy_data"]["agents"] = {"path": str(paths.legacy_agents), "count": 0, "exists": False}

    # Check legacy harness data
    if paths.legacy_harness.exists():
        harness_files = list(paths.legacy_harness.rglob("*"))
        result["legacy_data"]["harness"] = {
            "path": str(paths.legacy_harness),
            "file_count": len([f for f in harness_files if f.is_file()]),
        }
        report.add_step("inspect", "found_legacy_harness", source=paths.legacy_harness, details={"file_count": len([f for f in harness_files if f.is_file()])})
    else:
        result["legacy_data"]["harness"] = {"path": str(paths.legacy_harness), "exists": False}

    # Check Fleet v2 data
    if paths.fleet.exists():
        fleet_agent_files = list(paths.fleet_agents.glob("*.json")) if paths.fleet_agents.exists() else []
        bus_files = list(paths.fleet.glob("bus-*.jsonl"))
        snapshot = paths.fleet / "snapshot.json"
        result["fleet_v2_data"] = {
            "path": str(paths.fleet),
            "agent_count": len(fleet_agent_files),
            "bus_segments": len(bus_files),
            "has_snapshot": snapshot.exists(),
        }
        report.add_step("inspect", "found_fleet_v2", source=paths.fleet, details=result["fleet_v2_data"])
    else:
        result["fleet_v2_data"] = {"path": str(paths.fleet), "exists": False}

    # Check user data that should be preserved
    for p in paths.all_user_data_paths():
        name = p.name
        if p.exists():
            if p.is_file():
                size = p.stat().st_size
                result["user_data"][name] = {"path": str(p), "size": size, "exists": True}
            else:
                file_count = len([f for f in p.rglob("*") if f.is_file()])
                result["user_data"][name] = {"path": str(p), "file_count": file_count, "exists": True}
        else:
            result["user_data"][name] = {"path": str(p), "exists": False}

    # Recommendations
    if result["legacy_data"]["agents"].get("count", 0) > 0 and not result["fleet_v2_data"].get("exists", False):
        result["recommendations"].append("Migrate legacy agents to Fleet v2 roster (data/agents/ -> data/fleet/agents/)")

    if result["legacy_data"]["harness"].get("exists", False):
        result["recommendations"].append("Harness session data found at data/harness/ — consider archiving before Fleet v2 adoption")

    if not result["fleet_v2_data"].get("exists", False):
        result["recommendations"].append("Fleet v2 data directory does not exist — it will be created on first fleet operation")

    return result


def backup_user_data(paths: MigrationPaths, report: MigrationReport, backup_dir: Path) -> list[Path]:
    """Backup user data before migration."""
    backed_up = []
    backup_dir.mkdir(parents=True, exist_ok=True)

    for p in paths.all_user_data_paths():
        if p.exists():
            target = backup_dir / p.relative_to(paths.data)
            target.parent.mkdir(parents=True, exist_ok=True)
            if p.is_file():
                shutil.copy2(p, target)
            else:
                shutil.copytree(p, target, dirs_exist_ok=True)
            backed_up.append(target)
            report.add_step("backup", "copied", source=p, target=target)

    return backed_up


def migrate_legacy_agents(paths: MigrationPaths, report: MigrationReport, dry_run: bool = True) -> dict[str, Any]:
    """Migrate legacy agent data to Fleet v2 format."""
    result = {"migrated": 0, "skipped": 0, "errors": []}

    if not paths.legacy_agents.exists():
        report.add_step("migrate", "no_legacy_agents", details={"path": str(paths.legacy_agents)})
        return result

    if not paths.fleet_agents.exists() and not dry_run:
        paths.fleet_agents.mkdir(parents=True, exist_ok=True)

    agent_files = list(paths.legacy_agents.glob("*.json"))
    for agent_file in agent_files:
        try:
            data = json.loads(agent_file.read_text())
            agent_id = data.get("id") or data.get("agent_id") or agent_file.stem
            name = data.get("name") or f"agent-{agent_id[:8]}"

            fleet_agent = {
                "agent_id": agent_id,
                "name": name,
                "persona": data.get("persona") or data.get("system_prompt") or "",
                "provider": data.get("provider") or "groq",
                "model": data.get("model") or "",
                "key_name": data.get("key_name") or data.get("api_key_name"),
                "skills": data.get("skills") or [],
                "state": "IDLE",
                "created_at": data.get("created_at") or datetime.now().isoformat(),
                "last_activity": data.get("last_activity") or datetime.now().isoformat(),
                "memory": data.get("memory") or [],
                "stats": {
                    "tasks_done": data.get("tasks_done", 0),
                    "tasks_failed": data.get("tasks_failed", 0),
                    "tokens": data.get("tokens", 0),
                },
                "summary": "",
                "current_task": None,
            }

            target_file = paths.fleet_agents / f"{agent_id}.json"
            if not dry_run:
                target_file.write_text(json.dumps(fleet_agent, indent=2))

            result["migrated"] += 1
            report.add_step("migrate", "migrated_agent", source=agent_file, target=target_file, details={"agent_id": agent_id, "dry_run": dry_run})
        except Exception as e:
            result["skipped"] += 1
            result["errors"].append(f"{agent_file.name}: {e}")
            report.add_error(f"Failed to migrate {agent_file.name}: {e}")

    return result


def run_migration(args: argparse.Namespace) -> int:
    """Run the migration with the given arguments."""
    paths = MigrationPaths(Path(args.base_dir) if args.base_dir else None)
    report = MigrationReport()

    print(f"Hermus Fleet v2 Migration Tool")
    print(f"Base directory: {paths.base}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("-" * 60)

    # Phase 1: Inspect
    print("\n[1/4] Inspecting current data layout...")
    inspection = inspect(paths, report)
    print(f"  Legacy agents: {inspection['legacy_data'].get('agents', {}).get('count', 0)}")
    print(f"  Legacy harness: {inspection['legacy_data'].get('harness', {}).get('file_count', 0)} files")
    print(f"  Fleet v2 exists: {inspection['fleet_v2_data'].get('exists', False)}")
    if inspection["fleet_v2_data"].get("exists"):
        print(f"    Fleet agents: {inspection['fleet_v2_data'].get('agent_count', 0)}")
        print(f"    Bus segments: {inspection['fleet_v2_data'].get('bus_segments', 0)}")
    print(f"  User data preserved: {sum(1 for v in inspection['user_data'].values() if v.get('exists'))} paths")
    for rec in inspection["recommendations"]:
        print(f"  -> {rec}")

    if args.inspect_only:
        print("\nInspection complete. Exiting (--inspect-only).")
        report.save(paths.base / "migration-report.json")
        return 0

    # Phase 2: Backup
    if not args.dry_run:
        print("\n[2/4] Backing up user data...")
        backup_dir = paths.base / "data" / f"migration-backup-{int(time.time())}"
        backed_up = backup_user_data(paths, report, backup_dir)
        print(f"  Backed up {len(backed_up)} paths to {backup_dir}")
    else:
        print("\n[2/4] DRY RUN: Would backup user data")

    # Phase 3: Migrate
    print("\n[3/4] Migrating legacy data...")
    if args.dry_run:
        print("  DRY RUN: Simulating migration of legacy agents...")
        migrate_legacy_agents(paths, report, dry_run=True)
        print(f"  Would migrate {inspection['legacy_data'].get('agents', {}).get('count', 0)} agents")
    else:
        if not args.confirm:
            print("  ERROR: Migration requires --confirm flag. Exiting.")
            report.add_error("Migration aborted: --confirm not provided")
            report.save(paths.base / "migration-report.json")
            return 1
        result = migrate_legacy_agents(paths, report, dry_run=False)
        print(f"  Migrated: {result['migrated']}, Skipped: {result['skipped']}")
        if result["errors"]:
            for err in result["errors"]:
                print(f"  ERROR: {err}")

    # Phase 4: Verify
    print("\n[4/4] Verifying migration...")
    if paths.fleet_agents.exists():
        agent_files = list(paths.fleet_agents.glob("*.json"))
        print(f"  Fleet agents now: {len(agent_files)}")
        for f in agent_files[:5]:
            try:
                data = json.loads(f.read_text())
                print(f"    - {data.get('name', 'unnamed')} ({data.get('agent_id', 'no-id')}) state={data.get('state', '?')}")
            except Exception:
                print(f"    - {f.name} (parse error)")
        if len(agent_files) > 5:
            print(f"    ... and {len(agent_files) - 5} more")
    else:
        print("  Fleet agents directory not found")

    # Save report
    report.save(paths.base / "migration-report.json")
    print(f"\nMigration report saved to: {paths.base / 'migration-report.json'}")

    if report.errors:
        print(f"\nCompleted with {len(report.errors)} error(s).")
        return 1
    print("\nMigration completed successfully.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermus Fleet v2 migration tooling")
    parser.add_argument("--base-dir", type=str, help="Project base directory (default: repo root)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate migration without writing")
    parser.add_argument("--confirm", action="store_true", help="Confirm destructive migration (required for apply)")
    parser.add_argument("--inspect-only", action="store_true", help="Only inspect, do not migrate or backup")
    parser.add_argument("--json", action="store_true", help="Output inspection as JSON")
    return run_migration(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())