"""Transactional Rollback & Checkpoint Manager for Hermus.

Provides deterministic recovery points and transaction boundaries for agent development,
refactoring, and autonomous tasks. Features an explicit Git transaction state machine
(CREATED → ACTIVE → TESTING → VERIFIED → COMMITTING → MERGING → COMMITTED)
with crash recovery and automatic rollback on abort.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .workspace import workspace


class GitTxState(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    TESTING = "testing"
    VERIFIED = "verified"
    COMMITTING = "committing"
    MERGING = "merging"
    COMMITTED = "committed"
    ABORTING = "aborting"
    ABORTED = "aborted"


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


@dataclass
class Checkpoint:
    id: str
    label: str
    timestamp: str
    workspace_path: str
    files: dict[str, str] = field(default_factory=dict)  # rel_path -> sha256
    snapshot_dir: Optional[str] = None
    git_branch: Optional[str] = None
    git_commit: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        return cls(**data)


class RollbackManager:
    """Manages workspace snapshots, state recovery, and transactional Git workflows."""

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        workspace_dir: Optional[Path] = None,
    ):
        self.storage_dir = storage_dir or (workspace.root / "checkpoints")
        self.workspace_dir = workspace_dir or workspace.root
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._active_tx_file = self.storage_dir / "git_tx_active.json"
        self._active_git_tx: Optional[dict[str, Any]] = self._load_active_tx()

    def _load_active_tx(self) -> Optional[dict[str, Any]]:
        if self._active_tx_file.exists():
            try:
                return json.loads(self._active_tx_file.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _save_active_tx(self, tx: Optional[dict[str, Any]]) -> None:
        if tx is None:
            if self._active_tx_file.exists():
                try:
                    self._active_tx_file.unlink()
                except Exception:
                    pass
        else:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            self._active_tx_file.write_text(json.dumps(tx, indent=2), encoding="utf-8")

    def _ensure_git_exclude(self, root: Path) -> None:
        exclude_file = root / ".git" / "info" / "exclude"
        if exclude_file.parent.exists():
            try:
                existing = exclude_file.read_text(encoding="utf-8") if exclude_file.exists() else ""
                needed = ["checkpoints/", "git_tx_active.json", "artifacts/", "missions/", ".hermus/"]
                to_add = [item for item in needed if item not in existing]
                if to_add:
                    exclude_file.write_text(existing.rstrip() + "\n" + "\n".join(to_add) + "\n", encoding="utf-8")
            except Exception:
                pass

    def _ignore_file(self, rel_path: Path) -> bool:
        parts = rel_path.parts
        ignored_names = {
            ".git", "__pycache__", ".venv", "venv", "node_modules",
            ".mypy_cache", ".pytest_cache", ".tox", "dist", "build",
            ".hermus", "checkpoints", "artifacts", "missions"
        }
        return any(p in ignored_names or p.endswith(".pyc") for p in parts)

    def checkpoint(
        self,
        label: str,
        target_dir: Optional[Path] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Checkpoint:
        root = target_dir or self.workspace_dir
        cid = f"chk_{int(time.time())}_{os.urandom(3).hex()}"
        snapshot_path = self.storage_dir / cid
        snapshot_path.mkdir(parents=True, exist_ok=True)

        files_map: dict[str, str] = {}
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        rel = p.relative_to(root)
                        if self._ignore_file(rel):
                            continue
                        sha = _compute_sha256(p)
                        files_map[str(rel)] = sha

                        dest = snapshot_path / "files" / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, dest)
                    except Exception:
                        continue

        git_branch = None
        git_commit = None
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                git_branch = res.stdout.strip()
            res2 = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res2.returncode == 0:
                git_commit = res2.stdout.strip()
        except Exception:
            pass

        cp = Checkpoint(
            id=cid,
            label=label,
            timestamp=datetime.now().isoformat(),
            workspace_path=str(root),
            files=files_map,
            snapshot_dir=str(snapshot_path / "files"),
            git_branch=git_branch,
            git_commit=git_commit,
            metadata=metadata or {},
        )

        meta_file = snapshot_path / "meta.json"
        meta_file.write_text(json.dumps(cp.to_dict(), indent=2), encoding="utf-8")
        return cp

    def list_checkpoints(self) -> list[Checkpoint]:
        checkpoints: list[Checkpoint] = []
        if not self.storage_dir.exists():
            return checkpoints

        for p in self.storage_dir.iterdir():
            if p.is_dir():
                meta = p / "meta.json"
                if meta.exists():
                    try:
                        data = json.loads(meta.read_text(encoding="utf-8"))
                        checkpoints.append(Checkpoint.from_dict(data))
                    except Exception:
                        continue
        checkpoints.sort(key=lambda c: c.timestamp, reverse=True)
        return checkpoints

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        meta = self.storage_dir / checkpoint_id / "meta.json"
        if not meta.exists():
            return None
        try:
            return Checkpoint.from_dict(json.loads(meta.read_text(encoding="utf-8")))
        except Exception:
            return None

    def diff(self, checkpoint_id: str) -> dict[str, Any]:
        cp = self.get_checkpoint(checkpoint_id)
        if not cp:
            return {"success": False, "error": f"Checkpoint {checkpoint_id} not found"}

        root = Path(cp.workspace_path)
        current_files: dict[str, str] = {}
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        rel = p.relative_to(root)
                        if self._ignore_file(rel):
                            continue
                        current_files[str(rel)] = _compute_sha256(p)
                    except Exception:
                        continue

        old_files = set(cp.files.keys())
        new_files = set(current_files.keys())

        added = list(new_files - old_files)
        deleted = list(old_files - new_files)
        modified = [
            f for f in (old_files & new_files)
            if cp.files[f] != current_files[f]
        ]
        unchanged = [
            f for f in (old_files & new_files)
            if cp.files[f] == current_files[f]
        ]

        return {
            "success": True,
            "checkpoint_id": checkpoint_id,
            "label": cp.label,
            "added": added,
            "deleted": deleted,
            "modified": modified,
            "unchanged_count": len(unchanged),
            "has_changes": bool(added or deleted or modified),
        }

    def restore(self, checkpoint_id: str) -> dict[str, Any]:
        cp = self.get_checkpoint(checkpoint_id)
        if not cp:
            return {"success": False, "error": f"Checkpoint {checkpoint_id} not found"}

        root = Path(cp.workspace_path)
        snapshot_files_dir = Path(cp.snapshot_dir) if cp.snapshot_dir else (self.storage_dir / checkpoint_id / "files")
        if not snapshot_files_dir.exists():
            return {"success": False, "error": f"Snapshot directory {snapshot_files_dir} missing"}

        diff_info = self.diff(checkpoint_id)
        restored_files = []
        deleted_files = []

        for rel in diff_info.get("added", []):
            target = root / rel
            if target.exists():
                try:
                    target.unlink()
                    deleted_files.append(rel)
                except Exception:
                    pass

        for rel in list(cp.files.keys()):
            source = snapshot_files_dir / rel
            dest = root / rel
            if source.exists():
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                    restored_files.append(rel)
                except Exception:
                    continue

        return {
            "success": True,
            "checkpoint_id": checkpoint_id,
            "label": cp.label,
            "restored_count": len(restored_files),
            "deleted_count": len(deleted_files),
            "restored_files": restored_files,
            "deleted_files": deleted_files,
        }

    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        path = self.storage_dir / checkpoint_id
        if path.exists():
            try:
                shutil.rmtree(path)
                return True
            except Exception:
                return False
        return False

    # -- Hardened Git-aware transactional state machine -----------------
    def start_git_transaction(
        self,
        repo_dir: Optional[Path] = None,
        transaction_name: Optional[str] = None,
    ) -> dict[str, Any]:
        root = repo_dir or self.workspace_dir
        self._ensure_git_exclude(root)
        tx_id = transaction_name or f"tx_{int(time.time())}_{os.urandom(2).hex()}"
        branch_name = f"hermus/{tx_id}"

        try:
            res = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            original_branch = res.stdout.strip()

            subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )

            self._active_git_tx = {
                "id": tx_id,
                "state": GitTxState.ACTIVE.value,
                "branch": branch_name,
                "original_branch": original_branch,
                "repo_dir": str(root),
                "started_at": datetime.now().isoformat(),
            }
            self._save_active_tx(self._active_git_tx)
            return {"success": True, "transaction": self._active_git_tx}
        except Exception as e:
            return {"success": False, "error": f"Failed to start git transaction: {e}"}

    def transition_state(self, new_state: GitTxState) -> None:
        if self._active_git_tx:
            self._active_git_tx["state"] = new_state.value
            self._save_active_tx(self._active_git_tx)

    def commit_git_transaction(
        self,
        message: str = "Apply verified changes from Hermus agent",
    ) -> dict[str, Any]:
        if not self._active_git_tx:
            return {"success": False, "error": "No active git transaction"}

        tx = self._active_git_tx
        root = Path(tx["repo_dir"])
        tx_branch = tx["branch"]
        target_branch = tx["original_branch"]
        self._ensure_git_exclude(root)

        try:
            self.transition_state(GitTxState.COMMITTING)
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, timeout=10)
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.transition_state(GitTxState.MERGING)
            subprocess.run(["git", "checkout", target_branch], cwd=str(root), check=True, timeout=10)
            res_merge = subprocess.run(
                ["git", "merge", "--no-ff", tx_branch, "-m", f"Merge {tx_branch}: {message}"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=15,
            )
            subprocess.run(["git", "branch", "-D", tx_branch], cwd=str(root), capture_output=True)

            self.transition_state(GitTxState.COMMITTED)
            self._active_git_tx = None
            self._save_active_tx(None)

            return {
                "success": res_merge.returncode == 0,
                "target_branch": target_branch,
                "merged_branch": tx_branch,
                "output": res_merge.stdout + res_merge.stderr,
            }
        except Exception as e:
            self.abort_git_transaction()
            return {"success": False, "error": f"Failed to commit git transaction: {e}"}

    def abort_git_transaction(self) -> dict[str, Any]:
        if not self._active_git_tx:
            return {"success": False, "error": "No active git transaction"}

        tx = self._active_git_tx
        root = Path(tx["repo_dir"])
        tx_branch = tx["branch"]
        target_branch = tx["original_branch"]
        self._ensure_git_exclude(root)

        try:
            self.transition_state(GitTxState.ABORTING)
            subprocess.run(["git", "checkout", target_branch], cwd=str(root), check=True, timeout=10)
            subprocess.run(["git", "branch", "-D", tx_branch], cwd=str(root), capture_output=True)

            self.transition_state(GitTxState.ABORTED)
            self._active_git_tx = None
            self._save_active_tx(None)

            return {
                "success": True,
                "restored_branch": target_branch,
                "discarded_branch": tx_branch,
            }
        except Exception as e:
            self._active_git_tx = None
            self._save_active_tx(None)
            return {"success": False, "error": f"Failed to abort git transaction: {e}"}


rollback_manager = RollbackManager()
