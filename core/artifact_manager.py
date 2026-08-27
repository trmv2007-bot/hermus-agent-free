"""Artifact-Centric Workspace Manager for Hermus.

Treats tangible work products (APKs, ZIPs, wheels, binaries, test reports,
diffs, documentation, builds) as first-class objects tracked across mission
lifecycles, verifiable by domain engines, and surfaced to the UI.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .workspace import workspace


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


KNOWN_EXTENSIONS = {
    ".apk": ("apk", True),
    ".aab": ("aab", False),
    ".whl": ("wheel", False),
    ".tar.gz": ("archive", False),
    ".tgz": ("archive", False),
    ".zip": ("zip", False),
    ".pdf": ("document", True),
    ".html": ("report", True),
    ".htm": ("report", True),
    ".md": ("document", True),
    ".json": ("data", True),
    ".csv": ("data", True),
    ".diff": ("diff", True),
    ".patch": ("diff", True),
    ".png": ("image", True),
    ".jpg": ("image", True),
    ".svg": ("image", True),
    ".bin": ("binary", False),
    ".so": ("binary", False),
    ".exe": ("binary", False),
}


@dataclass
class Artifact:
    id: str
    name: str
    path: str
    rel_path: str
    artifact_type: str
    size_bytes: int
    sha256: str
    created_at: str
    mission_id: Optional[str] = None
    previewable: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Artifact:
        return cls(**data)


class ArtifactManager:
    def __init__(self, storage_dir: Optional[Path] = None, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or workspace.root
        self.storage_dir = storage_dir or (workspace.root / "artifacts")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_file = self.storage_dir / "manifest.json"

    def _load_manifest(self) -> Dict[str, Dict[str, Any]]:
        if not self._manifest_file.exists():
            return {}
        try:
            return json.loads(self._manifest_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_manifest(self, manifest: Dict[str, Dict[str, Any]]) -> None:
        self._manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def register_artifact(
        self,
        path: str | Path,
        name: Optional[str] = None,
        artifact_type: Optional[str] = None,
        mission_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        p = Path(path)
        if not p.is_absolute():
            p = (self.workspace_root / p).resolve()

        if not p.exists():
            raise FileNotFoundError(f"Artifact file not found: {p}")

        art_id = f"art_{int(datetime.now().timestamp())}_{os.urandom(2).hex()}"
        file_name = name or p.name
        suffix = p.suffix.lower()

        if artifact_type is None:
            detected_type, is_preview = KNOWN_EXTENSIONS.get(suffix, ("file", False))
        else:
            detected_type = artifact_type
            _, is_preview = KNOWN_EXTENSIONS.get(suffix, ("file", False))

        try:
            rel_path = str(p.relative_to(self.workspace_root))
        except ValueError:
            rel_path = str(p)

        art = Artifact(
            id=art_id,
            name=file_name,
            path=str(p),
            rel_path=rel_path,
            artifact_type=detected_type,
            size_bytes=p.stat().st_size,
            sha256=_sha256(p),
            created_at=datetime.now().isoformat(),
            mission_id=mission_id,
            previewable=is_preview,
            metadata=metadata or {},
        )

        manifest = self._load_manifest()
        manifest[art_id] = art.to_dict()
        self._save_manifest(manifest)
        return art

    def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        manifest = self._load_manifest()
        data = manifest.get(artifact_id)
        return Artifact.from_dict(data) if data else None

    def list_artifacts(
        self,
        mission_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
    ) -> List[Artifact]:
        manifest = self._load_manifest()
        results: List[Artifact] = []
        for d in manifest.values():
            art = Artifact.from_dict(d)
            if mission_id and art.mission_id != mission_id:
                continue
            if artifact_type and art.artifact_type != artifact_type:
                continue
            results.append(art)
        results.sort(key=lambda a: a.created_at, reverse=True)
        return results

    def scan_workspace(
        self,
        target_dir: Optional[Path] = None,
        mission_id: Optional[str] = None,
    ) -> List[Artifact]:
        """Scan workspace for build outputs, deliverables, and reports."""
        root = target_dir or self.workspace_root
        discovered: List[Artifact] = []

        scan_dirs = ["build", "dist", "out", "reports", "artifacts", "bin", "target"]
        paths_to_scan = [root] + [root / d for d in scan_dirs if (root / d).exists()]

        for base in paths_to_scan:
            for p in base.rglob("*"):
                if p.is_file() and p.suffix.lower() in KNOWN_EXTENSIONS:
                    if ".git" in p.parts or "node_modules" in p.parts or "__pycache__" in p.parts:
                        continue
                    try:
                        art = self.register_artifact(p, mission_id=mission_id)
                        discovered.append(art)
                    except Exception:
                        continue
        return discovered

    def export_bundle(
        self,
        output_zip_path: str | Path,
        mission_id: Optional[str] = None,
        artifact_ids: Optional[List[str]] = None,
    ) -> str:
        """Bundle mission artifacts into a single ZIP archive."""
        out_p = Path(output_zip_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        arts = self.list_artifacts(mission_id=mission_id)
        if artifact_ids:
            arts = [a for a in arts if a.id in artifact_ids]

        with zipfile.ZipFile(out_p, "w", zipfile.ZIP_DEFLATED) as zf:
            for art in arts:
                src = Path(art.path)
                if src.exists():
                    zf.write(src, arcname=art.name)
            # write manifest inside zip
            manifest_json = json.dumps([a.to_dict() for a in arts], indent=2)
            zf.writestr("artifacts_manifest.json", manifest_json)

        return str(out_p.resolve())


artifact_manager = ArtifactManager()
