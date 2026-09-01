"""Domain-Specific Verification Subsystem for Hermus.

Separates verification into Structural Verification (files, AST, configurations)
and Behavioral Verification (test execution, server responses, process liveness, artifacts).
A task is only verified when BOTH structural and behavioral proofs succeed.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .workspace import workspace


@dataclass
class VerificationResult:
    verified: bool
    score: float  # 0.0 to 1.0
    domain: str
    structural_verified: bool = True
    behavioral_verified: bool = True
    structural_score: float = 1.0
    behavioral_score: float = 1.0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseVerifier:
    domain: str = "generic"

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        raise NotImplementedError


class PythonVerifier(BaseVerifier):
    domain = "python"

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        root_dir = Path(context.get("workspace_dir") or context.get("target_dir") or workspace.root)
        files = context.get("files_modified") or context.get("files") or []
        execution_output = str(context.get("output") or context.get("result") or "")

        evidence: list[dict[str, Any]] = []
        errors: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []

        # --- A. STRUCTURAL VERIFICATION ---
        struct_passed = 0
        struct_total = 0

        py_files: list[Path] = []
        if files:
            for f in files:
                p = Path(f) if Path(f).is_absolute() else root_dir / f
                if p.suffix == ".py" and p.exists():
                    py_files.append(p)
        if not py_files and root_dir.exists():
            py_files = [p for p in root_dir.rglob("*.py") if not any(x in p.parts for x in (".git", ".venv", "venv", "__pycache__"))][:15]

        for p in py_files:
            struct_total += 1
            try:
                code = p.read_text(encoding="utf-8")
                ast.parse(code, filename=str(p))
                struct_passed += 1
                evidence.append({"type": "structural", "check": "ast_syntax", "file": str(p), "status": "valid"})
            except SyntaxError as e:
                errors.append(f"Syntax error in {p.name}:{e.lineno}: {e.msg}")
                suggestions.append(f"Fix Python syntax error at {p.name} line {e.lineno}")

        struct_score = (struct_passed / max(1, struct_total)) if struct_total > 0 else (1.0 if not errors else 0.0)
        structural_verified = bool(struct_score >= 0.8 and not errors)

        # --- B. BEHAVIORAL VERIFICATION ---
        behav_passed = 0
        behav_total = 0

        # 1. Output Traceback Check
        behav_total += 1
        error_markers = ("Traceback (most recent call last):", "ModuleNotFoundError:", "ImportError:", "NameError:", "TypeError:")
        found_markers = [m for m in error_markers if m in execution_output]
        if found_markers:
            errors.append(f"Runtime exceptions detected in output: {', '.join(found_markers)}")
            suggestions.append("Resolve the unhandled Python exceptions from execution output.")
        else:
            behav_passed += 1
            evidence.append({"type": "behavioral", "check": "runtime_output", "status": "clean"})

        # 2. Test Execution Check
        test_files = [p for p in py_files if "test" in p.name.lower()]
        if test_files:
            behav_total += 1
            try:
                res = subprocess.run(
                    [sys.executable, "-m", "pytest", "-v"] + [str(p) for p in test_files],
                    cwd=str(root_dir),
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if res.returncode == 0:
                    behav_passed += 1
                    evidence.append({"type": "behavioral", "check": "test_suite", "status": "passed", "output": res.stdout[:500]})
                else:
                    errors.append(f"Pytest failed with exit code {res.returncode}")
                    suggestions.append("Inspect test failures and patch the failing assertions.")
                    evidence.append({"type": "behavioral", "check": "test_suite", "status": "failed", "output": (res.stdout + res.stderr)[:1000]})
            except Exception as e:
                warnings.append(f"Could not execute test runner: {e}")

        behav_score = (behav_passed / max(1, behav_total)) if behav_total > 0 else 1.0
        behavioral_verified = bool(behav_score >= 0.8 and not any("Runtime exceptions" in e or "Pytest failed" in e for e in errors))

        total_score = round((struct_score * 0.4) + (behav_score * 0.6), 2)
        verified = bool(structural_verified and behavioral_verified)

        return VerificationResult(
            verified=verified,
            score=total_score,
            domain=self.domain,
            structural_verified=structural_verified,
            behavioral_verified=behavioral_verified,
            structural_score=round(struct_score, 2),
            behavioral_score=round(behav_score, 2),
            evidence=evidence,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            artifacts=[str(p) for p in py_files],
            details={"structural_checks": struct_total, "behavioral_checks": behav_total},
        )


class AndroidVerifier(BaseVerifier):
    domain = "android"

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        root_dir = Path(context.get("workspace_dir") or context.get("target_dir") or workspace.root)
        evidence: list[dict[str, Any]] = []
        errors: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []
        artifacts: list[str] = []

        struct_passed = 0
        manifest_files = list(root_dir.rglob("AndroidManifest.xml"))
        if manifest_files:
            struct_passed += 1
            manifest_p = manifest_files[0]
            evidence.append({"type": "structural", "check": "manifest_exists", "path": str(manifest_p)})
            content = manifest_p.read_text(encoding="utf-8", errors="ignore")
            if "package=" in content or "<manifest" in content:
                evidence.append({"type": "structural", "check": "manifest_valid", "package_declared": True})
        else:
            errors.append("Missing AndroidManifest.xml in Android project tree")
            suggestions.append("Ensure Android project has a standard AndroidManifest.xml under app/src/main/")

        gradle_files = list(root_dir.rglob("build.gradle*")) + list(root_dir.rglob("settings.gradle*"))
        if gradle_files:
            struct_passed += 1
            evidence.append({"type": "structural", "check": "gradle_build_scripts", "count": len(gradle_files)})

        struct_score = (struct_passed / 2.0)
        structural_verified = bool(struct_score >= 0.5)

        behav_passed = 0
        apk_files = list(root_dir.rglob("*.apk")) + list(root_dir.rglob("*.aab"))
        if apk_files:
            valid_apks = 0
            for apk in apk_files:
                if apk.stat().st_size > 1024:
                    try:
                        with zipfile.ZipFile(apk, "r") as z:
                            names = z.namelist()
                            if "AndroidManifest.xml" in names or any(n.endswith(".dex") for n in names) or "classes.dex" in names:
                                valid_apks += 1
                                artifacts.append(str(apk))
                                evidence.append({"type": "behavioral", "check": "apk_container_valid", "file": apk.name, "size": apk.stat().st_size})
                    except Exception:
                        pass
            if valid_apks > 0:
                behav_passed = 1
        else:
            if "apk" in str(context.get("task", "")).lower() or "build" in str(context.get("task", "")).lower():
                errors.append("No valid .apk or .aab binary artifact generated in workspace")
                suggestions.append("Run `./gradlew assembleDebug` to compile and package the APK.")

        behav_score = 1.0 if behav_passed else (0.5 if not ("apk" in str(context.get("task", "")).lower()) else 0.0)
        behavioral_verified = bool(behav_score >= 0.5)

        total_score = round((struct_score * 0.4) + (behav_score * 0.6), 2)
        verified = bool(structural_verified and behavioral_verified and not errors)

        return VerificationResult(
            verified=verified,
            score=total_score,
            domain=self.domain,
            structural_verified=structural_verified,
            behavioral_verified=behavioral_verified,
            structural_score=round(struct_score, 2),
            behavioral_score=round(behav_score, 2),
            evidence=evidence,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            artifacts=artifacts,
        )


class WebVerifier(BaseVerifier):
    domain = "web"

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        root_dir = Path(context.get("workspace_dir") or context.get("target_dir") or workspace.root)
        evidence: list[dict[str, Any]] = []
        errors: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []
        artifacts: list[str] = []

        html_files = list(root_dir.rglob("index.html")) + list(root_dir.rglob("*.html"))
        pkg_files = list(root_dir.rglob("package.json"))
        struct_ok = bool(html_files or pkg_files)
        if html_files:
            artifacts.append(str(html_files[0]))
            evidence.append({"type": "structural", "check": "html_entrypoint", "path": str(html_files[0])})
        elif pkg_files:
            evidence.append({"type": "structural", "check": "node_project", "path": str(pkg_files[0])})
        else:
            errors.append("No HTML entrypoint or package.json found")

        port = context.get("port")
        behav_ok = True
        if port:
            try:
                import urllib.request
                req = urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=2)
                if req.status in (200, 301, 302):
                    evidence.append({"type": "behavioral", "check": "http_response", "port": port, "status": req.status})
                else:
                    behav_ok = False
                    errors.append(f"HTTP server on port {port} returned status {req.status}")
            except Exception as e:
                behav_ok = False
                errors.append(f"HTTP server unreachable on port {port}: {e}")

        struct_score = 1.0 if struct_ok else 0.0
        behav_score = 1.0 if behav_ok else 0.0
        total_score = round((struct_score * 0.5) + (behav_score * 0.5), 2)
        verified = bool(struct_ok and behav_ok and not errors)

        return VerificationResult(
            verified=verified,
            score=total_score,
            domain=self.domain,
            structural_verified=struct_ok,
            behavioral_verified=behav_ok,
            evidence=evidence,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            artifacts=artifacts,
        )


class GitVerifier(BaseVerifier):
    domain = "git"

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        root_dir = Path(context.get("workspace_dir") or context.get("target_dir") or workspace.root)
        evidence: list[dict[str, Any]] = []
        errors: list[str] = []
        warnings: list[str] = []

        if not (root_dir / ".git").exists():
            return VerificationResult(verified=False, score=0.0, domain=self.domain, errors=["Not a git repository"])

        struct_ok = True
        behav_ok = True

        try:
            res = subprocess.run(["git", "status", "--porcelain"], cwd=str(root_dir), capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                uncommitted = [l for l in res.stdout.splitlines() if l.strip()]
                evidence.append({"type": "behavioral", "check": "git_status", "uncommitted_files": len(uncommitted)})
                if context.get("require_clean_tree") and uncommitted:
                    behav_ok = False
                    errors.append(f"Working tree has {len(uncommitted)} uncommitted changes")
        except Exception as e:
            behav_ok = False
            errors.append(f"git error: {e}")

        verified = bool(struct_ok and behav_ok and not errors)
        return VerificationResult(
            verified=verified, score=1.0 if verified else 0.4, domain=self.domain,
            structural_verified=struct_ok, behavioral_verified=behav_ok,
            evidence=evidence, errors=errors, warnings=warnings
        )


class LinuxVerifier(BaseVerifier):
    domain = "linux"

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        evidence: list[dict[str, Any]] = []
        errors: list[str] = []
        suggestions: list[str] = []

        struct_ok = True
        behav_ok = True

        target_file = context.get("file") or context.get("target_path")
        if target_file:
            p = Path(target_file)
            if p.exists():
                if os.access(p, os.X_OK):
                    evidence.append({"type": "behavioral", "check": "file_executable", "file": str(p)})
                else:
                    if context.get("require_executable"):
                        behav_ok = False
                        errors.append(f"File {p} lacks executable bit (+x)")

        port = context.get("port")
        if port:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.5)
            res = sock.connect_ex(("127.0.0.1", int(port)))
            sock.close()
            if res == 0:
                evidence.append({"type": "behavioral", "check": "port_listening", "port": port})
            else:
                behav_ok = False
                errors.append(f"Port {port} is not listening")

        verified = bool(struct_ok and behav_ok and not errors)
        return VerificationResult(
            verified=verified, score=1.0 if verified else 0.4, domain=self.domain,
            structural_verified=struct_ok, behavioral_verified=behav_ok,
            evidence=evidence, errors=errors, suggestions=suggestions
        )


class ResearchVerifier(BaseVerifier):
    domain = "research"

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        content = str(context.get("output") or context.get("result") or context.get("content") or "")
        evidence: list[dict[str, Any]] = []
        errors: list[str] = []
        warnings: list[str] = []

        has_headings = bool(re.search(r"^#+\s+|\n#+\s+", content))
        struct_ok = has_headings or bool(re.search(r"^\s*[-*•]\s+", content, re.MULTILINE))
        evidence.append({"type": "structural", "check": "structured_format", "status": struct_ok})

        word_count = len(content.split())
        substance_ok = word_count >= 50
        urls = re.findall(r"https?://[^\s\)\>]+", content)
        citation_markers = re.findall(r"\[\d+\]|\[source\]|\[ref\]", content, re.IGNORECASE)
        citations_present = bool(urls or citation_markers)

        evidence.append({"type": "behavioral", "check": "substance", "word_count": word_count})
        if citations_present:
            evidence.append({"type": "behavioral", "check": "citations_present", "count": len(urls) + len(citation_markers), "status": "passed"})

        if not substance_ok:
            errors.append(f"Research output too brief ({word_count} words; expected >= 50)")
        if not citations_present:
            warnings.append("No explicit citations or URLs found")

        behav_ok = substance_ok
        verified = bool(struct_ok and behav_ok and not errors)

        return VerificationResult(
            verified=verified,
            score=round((0.5 if struct_ok else 0.0) + (0.5 if behav_ok else 0.0), 2),
            domain=self.domain,
            structural_verified=struct_ok,
            behavioral_verified=behav_ok,
            evidence=evidence,
            errors=errors,
            warnings=warnings,
        )


class FileVerifier(BaseVerifier):
    domain = "file"

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        root_dir = Path(context.get("workspace_dir") or workspace.root)
        target = context.get("target_path") or context.get("file")
        evidence: list[dict[str, Any]] = []
        errors: list[str] = []
        artifacts: list[str] = []

        if not target:
            return VerificationResult(verified=False, score=0.0, domain=self.domain, errors=["No target file specified"])

        p = Path(target) if Path(target).is_absolute() else root_dir / target
        if not p.exists():
            return VerificationResult(verified=False, score=0.0, domain=self.domain, errors=[f"File {p} does not exist"])

        artifacts.append(str(p))
        size = p.stat().st_size
        evidence.append({"type": "structural", "check": "file_exists", "path": str(p), "size": size})

        struct_ok = True
        behav_ok = size > 0

        if size == 0:
            errors.append(f"File {p} is empty (0 bytes)")

        if p.suffix.lower() == ".json":
            try:
                json.loads(p.read_text(encoding="utf-8"))
                evidence.append({"type": "behavioral", "check": "json_valid", "status": True})
            except Exception as e:
                behav_ok = False
                errors.append(f"Invalid JSON syntax in {p.name}: {e}")

        verified = bool(struct_ok and behav_ok and not errors)
        return VerificationResult(
            verified=verified,
            score=1.0 if verified else 0.3,
            domain=self.domain,
            structural_verified=struct_ok,
            behavioral_verified=behav_ok,
            evidence=evidence,
            errors=errors,
            artifacts=artifacts,
        )


class GenericVerifier(BaseVerifier):
    domain = "generic"

    ERROR_MARKERS = (
        "syntaxerror", "traceback (most recent call last):", "fatal error",
        "command not found", "connection refused", "permission denied"
    )

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        text = str(context.get("output") or context.get("result") or "").strip()
        low = text.lower()
        problems = [m for m in self.ERROR_MARKERS if m in low]
        ok = bool(text) and not problems

        evidence = [{"type": "behavioral", "check": "marker_scan", "status": "clean" if ok else "failed", "problems_found": problems}]
        errors = [f"Problem marker detected: '{p}'" for p in problems] if problems else []

        return VerificationResult(
            verified=ok,
            score=1.0 if ok else (0.3 if text else 0.0),
            domain=self.domain,
            structural_verified=bool(text),
            behavioral_verified=ok,
            evidence=evidence,
            errors=errors,
        )


class VerifierRegistry:
    def __init__(self):
        self._verifiers: dict[str, BaseVerifier] = {
            "python": PythonVerifier(),
            "android": AndroidVerifier(),
            "web": WebVerifier(),
            "git": GitVerifier(),
            "linux": LinuxVerifier(),
            "research": ResearchVerifier(),
            "file": FileVerifier(),
            "generic": GenericVerifier(),
        }

    def register(self, domain: str, verifier: BaseVerifier) -> None:
        self._verifiers[domain.lower()] = verifier

    def get(self, domain: str) -> BaseVerifier:
        return self._verifiers.get(domain.lower(), self._verifiers["generic"])

    def list_domains(self) -> list[str]:
        return sorted(list(self._verifiers.keys()))

    def auto_detect_domain(self, task: str, files_modified: Optional[list[str]] = None, context: Optional[dict[str, Any]] = None) -> str:
        low = task.lower()
        files = files_modified or (context.get("files") if context else []) or []
        file_exts = {Path(f).suffix.lower() for f in files if f}

        if any(f in low for f in ("android", "apk", "aab", "gradle", "kotlin", "manifest.xml")) or ".apk" in file_exts or ".aab" in file_exts:
            return "android"
        if any(f in low for f in ("python", "pytest", "fastapi", "flask", "django", "pip", "def ", "class ")) or ".py" in file_exts:
            return "python"
        if any(f in low for f in ("web", "website", "html", "react", "vue", "css", "frontend", "javascript", "node")) or file_exts & {".html", ".css", ".js", ".jsx", ".tsx", ".ts"}:
            return "web"
        if any(f in low for f in ("git", "commit", "branch", "repo", "merge", "push", "pull request")):
            return "git"
        if any(f in low for f in ("research", "summarize", "find", "search", "investigate", "compare")):
            return "research"
        if any(f in low for f in ("file", "create", "write", "generate report", "save")):
            return "file"
        if any(f in low for f in ("linux", "daemon", "service", "port", "chmod", "systemctl")):
            return "linux"

        return "generic"

    def verify(
        self,
        domain_or_auto: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> VerificationResult:
        ctx = context or {}
        task = ctx.get("task", "")
        domain = domain_or_auto
        if not domain or domain == "auto":
            domain = self.auto_detect_domain(task, ctx.get("files_modified"), ctx)

        verifier = self.get(domain)
        return verifier.verify(ctx)

    def verify_multi(
        self,
        domains: list[str],
        context: Optional[dict[str, Any]] = None,
    ) -> VerificationResult:
        ctx = context or {}
        results = [self.get(d).verify(ctx) for d in domains]

        all_verified = all(r.verified for r in results)
        avg_score = sum(r.score for r in results) / max(1, len(results))
        combined_evidence = [e for r in results for e in r.evidence]
        combined_errors = [e for r in results for e in r.errors]
        combined_warnings = [w for r in results for w in r.warnings]
        combined_suggestions = [s for r in results for s in r.suggestions]
        combined_artifacts = list(set(a for r in results for a in r.artifacts))

        return VerificationResult(
            verified=all_verified,
            score=round(avg_score, 2),
            domain="composite(" + ",".join(domains) + ")",
            structural_verified=all(r.structural_verified for r in results),
            behavioral_verified=all(r.behavioral_verified for r in results),
            evidence=combined_evidence,
            errors=combined_errors,
            warnings=combined_warnings,
            suggestions=combined_suggestions,
            artifacts=combined_artifacts,
        )


verifier_registry = VerifierRegistry()
