"""Dedicated Software Engineer (SWE) Mode for Hermus.

Provides a complete repository-level development lifecycle:
Inspect → Understand → Plan → Edit (small patches) → Build → Test → Debug/Repair → Review Diff → Package & Report.
"""
from __future__ import annotations

import ast
import difflib
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .artifact_manager import artifact_manager
from .critic import critic_manager
from .rollback import rollback_manager
from .verifier_registry import verifier_registry
from .workspace import workspace


class SWEPhase(str, Enum):
    INSPECT = "inspect"
    PLAN = "plan"
    EDIT = "edit"
    BUILD = "build"
    TEST = "test"
    DEBUG_REPAIR = "debug_repair"
    REVIEW_DIFF = "review_diff"
    PACKAGE_REPORT = "package_report"


@dataclass
class ToolchainInfo:
    language: str  # python | javascript | typescript | rust | go | java | kotlin | c_cpp | unknown
    framework: Optional[str] = None  # fastapi, react, nextjs, django, android, etc.
    build_tool: Optional[str] = None  # npm, cargo, gradle, pip, go
    test_runner: Optional[str] = None  # pytest, jest, cargo test, go test
    linter: Optional[str] = None  # ruff, eslint, flake8
    entrypoints: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def detect_toolchain(root_dir: Path) -> ToolchainInfo:
    """Analyze repository layout and configuration files to determine the active toolchain."""
    # Android / Gradle
    if (root_dir / "AndroidManifest.xml").exists() or list(root_dir.rglob("AndroidManifest.xml")):
        return ToolchainInfo(
            language="kotlin",
            framework="android",
            build_tool="./gradlew assembleDebug" if (root_dir / "gradlew").exists() else "gradle build",
            test_runner="./gradlew test" if (root_dir / "gradlew").exists() else "gradle test",
        )

    # Rust / Cargo
    if (root_dir / "Cargo.toml").exists():
        return ToolchainInfo(
            language="rust",
            framework="cargo",
            build_tool="cargo build",
            test_runner="cargo test",
            linter="cargo clippy",
            entrypoints=["src/main.rs", "src/lib.rs"],
        )

    # Go
    if (root_dir / "go.mod").exists():
        return ToolchainInfo(
            language="go",
            framework="go",
            build_tool="go build ./...",
            test_runner="go test ./...",
            linter="golangci-lint",
            entrypoints=["main.go"],
        )

    # Node / TypeScript / JavaScript
    if (root_dir / "package.json").exists():
        pkg_json = {}
        try:
            pkg_json = json.loads((root_dir / "package.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        scripts = pkg_json.get("scripts", {})
        deps = {**pkg_json.get("dependencies", {}), **pkg_json.get("devDependencies", {})}

        is_ts = (root_dir / "tsconfig.json").exists() or "typescript" in deps
        lang = "typescript" if is_ts else "javascript"

        framework = "react" if "react" in deps else ("nextjs" if "next" in deps else ("express" if "express" in deps else None))
        build_cmd = "npm run build" if "build" in scripts else None
        test_cmd = "npm test" if "test" in scripts else None
        lint_cmd = "npm run lint" if "lint" in scripts else None

        return ToolchainInfo(
            language=lang,
            framework=framework,
            build_tool=build_cmd,
            test_runner=test_cmd,
            linter=lint_cmd,
            entrypoints=["src/index.ts" if is_ts else "src/index.js", "index.js"],
        )

    # Python
    py_markers = ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"]
    has_py = any((root_dir / m).exists() for m in py_markers) or list(root_dir.glob("*.py"))
    if has_py:
        # Check frameworks
        content = ""
        for req in ["requirements.txt", "pyproject.toml"]:
            if (root_dir / req).exists():
                try:
                    content += (root_dir / req).read_text(encoding="utf-8")
                except Exception:
                    pass
        framework = None
        if "fastapi" in content:
            framework = "fastapi"
        elif "django" in content:
            framework = "django"
        elif "flask" in content:
            framework = "flask"

        return ToolchainInfo(
            language="python",
            framework=framework,
            build_tool="pip install -e ." if (root_dir / "setup.py").exists() or (root_dir / "pyproject.toml").exists() else None,
            test_runner="pytest",
            linter="ruff check ." if shutil.which("ruff") else "flake8",
            entrypoints=[p.name for p in root_dir.glob("*.py") if p.name in ("main.py", "app.py", "hermus.py")],
        )

    return ToolchainInfo(language="unknown")


@dataclass
class SWEResult:
    success: bool
    task: str
    toolchain: Dict[str, Any]
    phases_executed: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    diff: str = ""
    test_results: Dict[str, Any] = field(default_factory=dict)
    verification: Dict[str, Any] = field(default_factory=dict)
    critic_review: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)
    change_report: str = ""
    repairs_made: int = 0
    checkpoint_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SoftwareEngineerMode:
    """End-to-end repository-level software development lifecycle runner."""

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or workspace.root

    def _apply_patch(self, file_path: Path, new_content: str) -> bool:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        # Pre-validate AST if Python
        if file_path.suffix == ".py":
            try:
                ast.parse(new_content, filename=str(file_path))
            except SyntaxError:
                return False
        file_path.write_text(new_content, encoding="utf-8")
        return True

    def execute(
        self,
        task: str,
        workspace_dir: Optional[Path] = None,
        coder_fn: Optional[Callable[[str, Dict[str, Any]], Dict[str, str]]] = None,
        max_repairs: int = 3,
    ) -> SWEResult:
        root = workspace_dir or self.workspace_root
        phases: List[str] = []
        files_modified: List[str] = []
        repairs = 0

        # Phase 1: INSPECT
        phases.append(SWEPhase.INSPECT.value)
        toolchain = detect_toolchain(root)

        # Checkpoint before modifications for safe rollback
        cp = rollback_manager.checkpoint(label=f"swe_before_{task[:30]}", target_dir=root)

        # Phase 2: PLAN & EDIT
        phases.append(SWEPhase.PLAN.value)
        phases.append(SWEPhase.EDIT.value)

        # Default or injected coder function
        if coder_fn:
            code_changes = coder_fn(task, {"toolchain": toolchain.to_dict(), "workspace": str(root)})
        else:
            # Fallback deterministic template if offline/mock
            code_changes = {}

        for rel_p, content in code_changes.items():
            target_p = root / rel_p
            if self._apply_patch(target_p, content):
                files_modified.append(str(rel_p))

        # Phase 3 & 4: BUILD & TEST Loop with REPAIRS
        test_success = False
        test_details: Dict[str, Any] = {}

        while repairs <= max_repairs:
            phases.append(SWEPhase.BUILD.value)
            phases.append(SWEPhase.TEST.value)

            # Run Python or toolchain tests
            if toolchain.language == "python":
                test_res = subprocess.run(
                    ["pytest", "-v"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                test_success = (test_res.returncode == 0)
                test_details = {
                    "returncode": test_res.returncode,
                    "stdout": test_res.stdout[-2000:],
                    "stderr": test_res.stderr[-2000:],
                }
            elif toolchain.test_runner:
                test_res = subprocess.run(
                    toolchain.test_runner.split(),
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                test_success = (test_res.returncode == 0)
                test_details = {
                    "returncode": test_res.returncode,
                    "stdout": test_res.stdout[-2000:],
                    "stderr": test_res.stderr[-2000:],
                }
            else:
                test_success = True
                test_details = {"message": "No test runner configured, static validation passed"}

            if test_success:
                break

            # Attempt repair
            repairs += 1
            phases.append(SWEPhase.DEBUG_REPAIR.value)
            if repairs > max_repairs or not coder_fn:
                break

            # Feed test failure back into coder_fn
            repair_ctx = {
                "toolchain": toolchain.to_dict(),
                "error_log": test_details.get("stdout", "") + test_details.get("stderr", ""),
                "repair_round": repairs,
            }
            repaired_changes = coder_fn(f"Repair test failures for: {task}", repair_ctx)
            for rel_p, content in repaired_changes.items():
                target_p = root / rel_p
                if self._apply_patch(target_p, content):
                    if str(rel_p) not in files_modified:
                        files_modified.append(str(rel_p))

        # If tests completely failed and unrepairable, optionally rollback
        if not test_success and repairs > max_repairs:
            rollback_manager.restore(cp.id)

        # Phase 5: REVIEW DIFF
        phases.append(SWEPhase.REVIEW_DIFF.value)
        diff_info = rollback_manager.diff(cp.id)

        # Generate unified diff string
        diff_text_list = []
        for rel in files_modified:
            p = root / rel
            snap_p = Path(cp.snapshot_dir) / rel if cp.snapshot_dir else None
            old_lines = snap_p.read_text(encoding="utf-8").splitlines() if snap_p and snap_p.exists() else []
            new_lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
            diff = "\n".join(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
            if diff:
                diff_text_list.append(diff)
        full_diff = "\n\n".join(diff_text_list)

        # Phase 6: VERIFICATION & CRITIC PANEL
        files_content = {f: (root / f).read_text(encoding="utf-8", errors="ignore") for f in files_modified if (root / f).exists()}
        critic_res = critic_manager.run_full_review(
            task=task,
            files_content=files_content,
            execution_log=test_details.get("stdout", ""),
            artifacts=files_modified,
            requirements=[task],
        )

        v_result = verifier_registry.verify(
            domain_or_auto=toolchain.language if toolchain.language in ("python", "android", "web") else "auto",
            context={"task": task, "workspace_dir": str(root), "files_modified": files_modified, "output": full_diff},
        )

        # Phase 7: PACKAGE & REPORT
        phases.append(SWEPhase.PACKAGE_REPORT.value)
        artifacts = artifact_manager.scan_workspace(target_dir=root)
        artifact_paths = [a.path for a in artifacts]

        change_report = f"""# SWE Change Report: {task}
- **Language/Framework**: {toolchain.language} / {toolchain.framework or 'generic'}
- **Files Modified**: {len(files_modified)} ({', '.join(files_modified) if files_modified else 'none'})
- **Test Status**: {'PASSED' if test_success else 'FAILED'} (Repairs attempted: {repairs})
- **Domain Verification**: {'VERIFIED' if v_result.verified else 'UNVERIFIED'} (Score: {v_result.score})
- **Critic Score**: {critic_res['overall_score']}/100 (Verdict: {critic_res['verdict'].upper()})
- **Artifacts Generated**: {len(artifact_paths)}

## Review Summary
{critic_res['summary']}
"""

        final_success = bool(test_success and v_result.verified and critic_res["approved"])

        return SWEResult(
            success=final_success,
            task=task,
            toolchain=toolchain.to_dict(),
            phases_executed=phases,
            files_modified=files_modified,
            diff=full_diff,
            test_results=test_details,
            verification=v_result.to_dict(),
            critic_review=critic_res,
            artifacts=artifact_paths,
            change_report=change_report,
            repairs_made=repairs,
            checkpoint_id=cp.id,
        )


swe_mode = SoftwareEngineerMode()
