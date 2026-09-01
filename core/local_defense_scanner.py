"""Read-only local folder defensive scanner.

This scanner is intentionally conservative and privacy-preserving: it walks an
approved local folder, inspects metadata and a tiny prefix of text-like files,
and returns indicators/summaries only. It never uploads files, executes files,
quarantines files, deletes files, or returns file contents.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import CommandStatus, EventEnvelope, EventType

ROOT = Path(__file__).resolve().parents[1]

SUSPICIOUS_EXTENSIONS = {
    ".exe", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", ".wsf",
    ".jar", ".apk", ".appimage", ".dmg", ".pkg", ".deb", ".rpm", ".msi",
}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"}
TEXT_EXTENSIONS = {".txt", ".md", ".log", ".csv", ".json", ".xml", ".html", ".js", ".ps1", ".bat", ".cmd"}
INDICATOR_PATTERNS = [
    ("powershell_download", re.compile(r"powershell.*(downloadstring|invoke-webrequest|iwr\s+http)", re.I | re.S)),
    ("curl_pipe_shell", re.compile(r"(curl|wget)\s+[^\n|;&]+\|\s*(sh|bash|powershell)", re.I)),
    ("encoded_powershell", re.compile(r"powershell[^\n]{0,120}-(enc|encodedcommand)\b", re.I)),
    ("autorun_reference", re.compile(r"\b(autorun\.inf|startup|runonce)\b", re.I)),
    ("credential_theft_terms", re.compile(r"\b(keylogger|steal(?:er)?|token grabber|cookie grabber|credential dump)\b", re.I)),
]


@dataclass
class ScanFinding:
    path: str
    severity: str
    reason: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocalFolderScanReport:
    root: str
    scanned_files: int = 0
    skipped_files: int = 0
    findings: list[ScanFinding] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": True,
            "root": self.root,
            "scanned_files": self.scanned_files,
            "skipped_files": self.skipped_files,
            "finding_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
            "generated_at": self.generated_at,
            "privacy": "No file contents are returned; only paths, metadata-derived reasons, and short indicator labels.",
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Local folder defensive scan — {self.root}",
            "",
            f"Generated: {self.generated_at}",
            f"Scanned files: {self.scanned_files}",
            f"Skipped files: {self.skipped_files}",
            f"Findings: {len(self.findings)}",
            "",
            "## Findings",
            "",
        ]
        if not self.findings:
            lines.append("No suspicious indicators found in the sampled files.")
        else:
            lines += ["| severity | path | reason | evidence |", "| --- | --- | --- | --- |"]
            for finding in self.findings:
                lines.append(f"| {finding.severity} | `{_cell(finding.path)}` | {_cell(finding.reason)} | {_cell(finding.evidence)} |")
        lines += ["", "## Privacy", "", "This scan is read-only and reports indicators only; it does not return file contents, upload files, delete files, or execute files.", ""]
        return "\n".join(lines)


def scan_folder(
    path: str,
    *,
    max_files: int = 500,
    max_bytes: int = 4096,
    follow_symlinks: bool = False,
    save_report: bool = False,
    mission_id: str = "",
    output_dir: str = "",
) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        return {"success": False, "error": f"path does not exist: {path}"}
    if not root.is_dir():
        return {"success": False, "error": f"path is not a directory: {path}"}
    report = LocalFolderScanReport(root=str(root))
    limit = max(1, min(int(max_files or 500), 5000))
    sample_bytes = max(256, min(int(max_bytes or 4096), 65536))
    for dirpath, dirnames, filenames in os.walk(root, followlinks=bool(follow_symlinks)):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "__pycache__", ".venv", ".cache"}]
        for filename in filenames:
            if report.scanned_files >= limit:
                report.skipped_files += 1
                continue
            fpath = Path(dirpath) / filename
            try:
                if fpath.is_symlink() and not follow_symlinks:
                    report.skipped_files += 1
                    continue
                _scan_file(root, fpath, report, sample_bytes)
                report.scanned_files += 1
            except (OSError, PermissionError):
                report.skipped_files += 1
    out = report.to_dict()
    out["markdown"] = report.to_markdown()
    if save_report:
        saved = save_scan_report(out, mission_id=mission_id, output_dir=output_dir)
        out["report_artifact"] = saved.get("artifact")
        out["report_path"] = saved.get("path")
        out["report_saved"] = bool(saved.get("success"))
        if mission_id:
            _attach_scan_to_mission(mission_id, out)
    _register_scanner_configured()
    _publish_scan(out)
    return out


def reports_dir() -> Path:
    root = ROOT / "data" / "local_defense_reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_scan_reports(limit: int = 50) -> list[dict[str, Any]]:
    root = reports_dir()
    rows = []
    for path in sorted(root.glob("local-defense-scan-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:max(1, min(int(limit or 50), 200))]:
        rows.append({
            "name": path.name,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "url": f"/local-defense/reports/{path.name}",
        })
    return rows


def read_scan_report(name: str) -> dict[str, Any]:
    root = reports_dir().resolve()
    safe_name = Path(str(name or "")).name
    path = (root / safe_name).resolve()
    if not str(path).startswith(str(root)) or not path.exists() or path.suffix.lower() != ".md":
        return {"success": False, "error": "report not found", "name": safe_name}
    return {"success": True, "name": path.name, "path": str(path), "markdown": path.read_text(encoding="utf-8")}


def save_scan_report(result: dict[str, Any], *, mission_id: str = "", output_dir: str = "") -> dict[str, Any]:
    try:
        root = Path(output_dir).expanduser() if output_dir else reports_dir()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe_mid = re.sub(r"[^A-Za-z0-9_.-]+", "_", mission_id or "manual")
        path = root / f"local-defense-scan-{safe_mid}-{stamp}.md"
        path.write_text(result.get("markdown") or "", encoding="utf-8")
        artifact = None
        try:
            from .artifact_manager import artifact_manager

            art = artifact_manager.register_artifact(
                path,
                name=path.name,
                artifact_type="report",
                mission_id=mission_id or None,
                metadata={
                    "kind": "local_defense_scan",
                    "root": result.get("root"),
                    "finding_count": result.get("finding_count"),
                    "scanned_files": result.get("scanned_files"),
                },
            )
            artifact = art.to_dict() if hasattr(art, "to_dict") else asdict(art)
        except Exception:
            artifact = {"path": str(path), "artifact_type": "report", "mission_id": mission_id or None}
        return {"success": True, "path": str(path), "artifact": artifact}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def _attach_scan_to_mission(mission_id: str, result: dict[str, Any]) -> None:
    try:
        from .mission import mission_engine

        report = mission_engine.get_mission(mission_id)
        if not report:
            return
        artifact_id = (result.get("report_artifact") or {}).get("id") or result.get("report_path")
        if artifact_id and artifact_id not in report.artifacts:
            report.artifacts.append(artifact_id)
        report.evidence.append({
            "stage": "local_defense_scan",
            "status": "completed",
            "root": result.get("root"),
            "scanned_files": result.get("scanned_files"),
            "finding_count": result.get("finding_count"),
            "artifact": artifact_id,
        })
        mission_engine._save_mission(report)
    except Exception:
        pass


def _register_scanner_configured() -> None:
    try:
        from .capability_registry import get_capability_registry

        get_capability_registry().register(
            "Local folder defensive scanner",
            category="private_data_scope",
            status="configured",
            source="local_defense_scan",
            notes="Read-only scanner available; broad/private folders still require scoped approval.",
        )
    except Exception:
        pass


def _scan_file(root: Path, fpath: Path, report: LocalFolderScanReport, sample_bytes: int) -> None:
    rel = str(fpath.relative_to(root)) if _is_relative_to(fpath, root) else fpath.name
    suffix = fpath.suffix.lower()
    try:
        stat = fpath.stat()
    except OSError:
        report.skipped_files += 1
        return
    if suffix in SUSPICIOUS_EXTENSIONS:
        report.findings.append(ScanFinding(rel, "medium", "suspicious executable/script extension", suffix))
    if suffix in ARCHIVE_EXTENSIONS and stat.st_size > 100 * 1024 * 1024:
        report.findings.append(ScanFinding(rel, "low", "large archive may need manual review", _size(stat.st_size)))
    if fpath.name.startswith(".") and suffix in SUSPICIOUS_EXTENSIONS:
        report.findings.append(ScanFinding(rel, "medium", "hidden executable/script file", suffix))
    if stat.st_mode & 0o111 and suffix not in {"", ".sh", ".py"}:
        report.findings.append(ScanFinding(rel, "low", "file has executable bit with unusual extension", suffix or "no extension"))
    if suffix in TEXT_EXTENSIONS and stat.st_size <= 2 * 1024 * 1024:
        sample = _read_sample(fpath, sample_bytes)
        for label, pattern in INDICATOR_PATTERNS:
            if pattern.search(sample):
                report.findings.append(ScanFinding(rel, "high", "suspicious text indicator", label))
        if _high_entropy_line(sample):
            report.findings.append(ScanFinding(rel, "low", "contains a long high-entropy line; review locally if unexpected", "content not shown"))


def _read_sample(path: Path, n: int) -> str:
    try:
        return path.read_bytes()[:n].decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _high_entropy_line(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 120 or len(s) > 2000:
            continue
        alphabet = set(s)
        if len(alphabet) < 40:
            continue
        probs = [s.count(ch) / len(s) for ch in alphabet]
        entropy = -sum(p * math.log2(p) for p in probs)
        if entropy > 4.5:
            return True
    return False


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _cell(value: object) -> str:
    return str(value or "").replace("\n", " ").replace("|", "/")[:240]


def _publish_scan(data: dict[str, Any]) -> None:
    try:
        from .events import get_bus

        get_bus().publish(EventEnvelope(
            type=EventType.STATE_CHANGED.value,
            command="local_defense.scan.completed",
            args_redacted={"root": data.get("root"), "scanned_files": data.get("scanned_files"), "finding_count": data.get("finding_count")},
            status=CommandStatus.SUCCEEDED.value,
        ))
    except Exception:
        pass


__all__ = [
    "LocalFolderScanReport",
    "ScanFinding",
    "list_scan_reports",
    "read_scan_report",
    "reports_dir",
    "save_scan_report",
    "scan_folder",
]
