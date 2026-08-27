"""Transactional Rollback & Checkpoint Manager for Hermus.

Provides deterministic recovery points and transaction boundaries for agent development,
refactoring, and autonomous tasks. Supports both file-tree snapshotting and isolated
Git branch transactions.
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
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .workspace import workspace


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
    files: Dict[str, str] = field(default_factory=dict)  # rel_path -> sha256
    snapshot_dir: Optional[str] = None
    git_branch: Optional[str] = None
    git_commit: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Checkpoint:
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
        self._active_git_tx: Optional[Dict[str, Any]] = None

    def _ignore_file(self, rel_path: Path) -> bool:
        parts = rel_path.parts
        ignored_names = {
            ".git", "__pycache__", ".venv", "venv", "node_modules",
            ".mypy_cache", ".pytest_cache", ".tox", "dist", "build",
            ".hermus", "checkpoints", "artifacts"
        }
        return any(p in ignored_names or p.endswith(".pyc") for p in parts)

    def checkpoint(
        self,
        label: str,
        target_dir: Optional[Path] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Checkpoint:
        """Create a file-level snapshot of the target directory / workspace."""
        root = target_dir or self.workspace_dir
        cid = f"chk_{int(time.time())}_{os.urandom(3).hex()}"
        snapshot_path = self.storage_dir / cid
        snapshot_path.mkdir(parents=True, exist_ok=True)

        files_map: Dict[str, str] = {}
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        rel = p.relative_to(root)
                        if self._ignore_file(rel):
                            continue
                        sha = _compute_sha256(p)
                        files_map[str(rel)] = sha

                        # Copy file to snapshot store
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

    def list_checkpoints(self) -> List[Checkpoint]:
        """List all saved checkpoints ordered by timestamp descending."""
        checkpoints: List[Checkpoint] = []
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

    def diff(self, checkpoint_id: str) -> Dict[str, Any]:
        """Compare current workspace state with checkpoint state."""
        cp = self.get_checkpoint(checkpoint_id)
        if not cp:
            return {"success": False, "error": f"Checkpoint {checkpoint_id} not found"}

        root = Path(cp.workspace_path)
        current_files: Dict[str, str] = {}
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

    def restore(self, checkpoint_id: str) -> Dict[str, Any]:
        """Restore the workspace files to the exact state saved in the checkpoint."""
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

        # Remove files added since checkpoint
        for rel in diff_info.get("added", []):
            target = root / rel
            if target.exists():
                try:
                    target.unlink()
                    deleted_files.append(rel)
                except Exception:
                    pass

        # Restore modified and deleted files from snapshot
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

    # -- Git-aware transactional branch workflow ------------------------
    def start_git_transaction(
        self,
        repo_dir: Optional[Path] = None,
        transaction_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a dedicated working branch to isolate experimental modifications."""
        root = repo_dir or self.workspace_dir
        tx_id = transaction_name or f"tx_{int(time.time())}_{os.urandom(2).hex()}"
        branch_name = f"hermus/{tx_id}"

        try:
            # Get current branch
            res = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            original_branch = res.stdout.strip()

            # Create and switch to new transaction branch
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
                "branch": branch_name,
                "original_branch": original_branch,
                "repo_dir": str(root),
                "started_at": datetime.now().isoformat(),
            }
            return {"success": True, "transaction": self._active_git_tx}
        except Exception as e:
            return {"success": False, "error": f"Failed to start git transaction: {e}"}

    def commit_git_transaction(
        self,
        message: str = "Apply verified changes from Hermus agent",
    ) -> Dict[str, Any]:
        """Commit current changes and merge transaction branch into original branch."""
        if not self._active_git_tx:
            return {"success": False, "error": "No active git transaction"}

        tx = self._active_git_tx
        root = Path(tx["repo_dir"])
        tx_branch = tx["branch"]
        target_branch = tx["original_branch"]

        try:
            # Stage all changes
            subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, timeout=10)
            # Commit if changes exist
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Switch back to original branch
            subprocess.run(["git", "checkout", target_branch], cwd=str(root), check=True, timeout=10)
            # Merge transaction branch
            res_merge = subprocess.run(
                ["git", "merge", "--no-ff", tx_branch, "-m", f"Merge {tx_branch}: {message}"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=15,
            )
            # Delete transaction branch
            subprocess.run(["git", "branch", "-D", tx_branch], cwd=str(root), capture_output=True)

            self._active_git_tx = None
            return {
                "success": res_merge.returncode == 0,
                "target_branch": target_branch,
                "merged_branch": tx_branch,
                "output": res_merge.stdout + res_merge.stderr,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to commit git transaction: {e}"}

    def abort_git_transaction(self) -> Dict[str, Any]:
        """Discard changes on transaction branch and return to original branch."""
        if not self._active_git_tx:
            return {"success": False, "error": "No active git transaction"}

        tx = self._active_git_tx
        root = Path(tx["repo_dir"])
        tx_branch = tx["branch"]
        target_branch = tx["original_branch"]

        try:
            # Switch back to original branch
            subprocess.run(["git", "checkout", target_branch], cwd=str(root), check=True, timeout=10)
            # Force delete transaction branch
            subprocess.run(["git", "branch", "-D", tx_branch], cwd=str(root), capture_output=True)

            self._active_git_tx = None
            return {
                "success": True,
                "restored_branch": target_branch,
                "discarded_branch": tx_branch,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to abort git transaction: {e}"}


rollback_manager = RollbackManager()
