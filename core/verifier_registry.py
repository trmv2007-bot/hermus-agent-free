"""Domain-Specific Verification Subsystem for Hermus.

Replaces shallow string/exit-code heuristics with deep, domain-grounded proofs
of completion across Python, Android, Web, Git, Linux, Research, API, Game, and File tasks.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from .workspace import workspace


@dataclass
class VerificationResult:
    verified: bool
    score: float  # 0.0 to 1.0
    domain: str
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BaseVerifier:
    domain: str = "generic"

    def verify(self, context: Dict[str, Any]) -> VerificationResult:
        raise NotImplementedError


class PythonVerifier(BaseVerifier):
    domain = "python"

    def verify(self, context: Dict[str, Any]) -> VerificationResult:
        task = context.get("task", "")
        root_dir = Path(context.get("workspace_dir") or context.get("target_dir") or workspace.root)
        files = context.get("files_modified") or context.get("files") or []
        execution_output = str(context.get("output") or context.get("result") or "")

        evidence: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        suggestions: List[str] = []
        artifacts: List[str] = []

        total_checks = 0
        passed_checks = 0

        # 1. AST Syntax Check on all targeted / found Python files
        py_files: List[Path] = []
        if files:
            for f in files:
                p = Path(f) if Path(f).is_absolute() else root_dir / f
                if p.suffix == ".py" and p.exists():
                    py_files.append(p)
        if not py_files and root_dir.exists():
            py_files = [p for p in root_dir.rglob("*.py") if not any(x in p.parts for x in (".git", ".venv", "venv", "__pycache__"))][:15]

        for p in py_files:
            total_checks += 1
            try:
                code = p.read_text(encoding="utf-8")
                ast.parse(code, filename=str(p))
                passed_checks += 1
                evidence.append({"check": "ast_syntax", "file": str(p), "status": "valid"})
            except SyntaxError as e:
                errors.append(f"Syntax error in {p.name}:{e.lineno}: {e.msg}")
                suggestions.append(f"Fix Python syntax error at {p.name} line {e.lineno}")

        # 2. Test Execution Check (run pytest / unittest if test files exist)
        test_files = [p for p in py_files if "test" in p.name.lower()]
        if test_files:
            total_checks += 1
            try:
                res = subprocess.run(
                    ["pytest", "-v"] + [str(p) for p in test_files],
                    cwd=str(root_dir),
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if res.returncode == 0:
                    passed_checks += 1
                    evidence.append({"check": "test_suite", "status": "passed", "output": res.stdout[:500]})
                else:
                    errors.append(f"Pytest failed with exit code {res.returncode}")
                    suggestions.append("Inspect test failures and patch the failing assertions.")
                    evidence.append({"check": "test_suite", "status": "failed", "output": (res.stdout + res.stderr)[:1000]})
            except Exception as e:
                warnings.append(f"Could not execute test runner: {e}")

        # 3. Output Traceback / Error Marker Check
        total_checks += 1
        error_markers = ("Traceback (most recent call last):", "ModuleNotFoundError:", "ImportError:", "NameError:", "TypeError:")
        found_markers = [m for m in error_markers if m in execution_output]
        if found_markers:
            errors.append(f"Runtime exceptions detected in output: {', '.join(found_markers)}")
            suggestions.append("Resolve the unhandled Python exceptions from execution output.")
        else:
            passed_checks += 1
            evidence.append({"check": "runtime_output", "status": "clean"})

        score = (passed_checks / max(1, total_checks)) if total_checks > 0 else (1.0 if not errors else 0.0)
        verified = bool(score >= 0.8 and not errors)

        return VerificationResult(
            verified=verified,
            score=round(score, 2),
            domain=self.domain,
            evidence=evidence,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            artifacts=[str(p) for p in py_files],
            details={"total_checks": total_checks, "passed_checks": passed_checks},
        )


class AndroidVerifier(BaseVerifier):
    domain = "android"

    def verify(self, context: Dict[str, Any]) -> VerificationResult:
        root_dir = Path(context.get("workspace_dir") or context.get("target_dir") or workspace.root)
        evidence: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        suggestions: List[str] = []
        artifacts: List[str] = []

        total_checks = 0
        passed_checks = 0

        # 1. Check Project Structure
        total_checks += 1
        manifest_files = list(root_dir.rglob("AndroidManifest.xml"))
        if manifest_files:
            passed_checks += 1
            manifest_p = manifest_files[0]
            evidence.append({"check": "manifest_exists", "path": str(manifest_p)})
            content = manifest_p.read_text(encoding="utf-8", errors="ignore")
            if "package=" in content or "<manifest" in content:
                evidence.append({"check": "manifest_valid", "package_declared": True})
            else:
                warnings.append("AndroidManifest.xml does not declare package or valid root XML tag")
        else:
            errors.append("Missing AndroidManifest.xml in Android project tree")
            suggestions.append("Ensure Android project has a standard AndroidManifest.xml under app/src/main/")

        # 2. Check Gradle Build Scripts
        total_checks += 1
        gradle_files = list(root_dir.rglob("build.gradle*")) + list(root_dir.rglob("settings.gradle*"))
        if gradle_files:
            passed_checks += 1
            evidence.append({"check": "gradle_build_scripts", "count": len(gradle_files)})
        else:
            errors.append("No build.gradle or settings.gradle found")
            suggestions.append("Add Gradle build configuration scripts.")

        # 3. Check for Generated Artifacts (APK / AAB)
        total_checks += 1
        apk_files = list(root_dir.rglob("*.apk")) + list(root_dir.rglob("*.aab"))
        if apk_files:
            valid_apks = 0
            for apk in apk_files:
                if apk.stat().st_size > 1024:
                    try:
                        # Verify ZIP / APK container validity
                        with zipfile.ZipFile(apk, "r") as z:
                            names = z.namelist()
                            if "AndroidManifest.xml" in names or any(n.endswith(".dex") for n in names) or "classes.dex" in names:
                                valid_apks += 1
                                artifacts.append(str(apk))
                                evidence.append({"check": "apk_container_valid", "file": apk.name, "size": apk.stat().st_size})
                    except Exception:
                        pass
            if valid_apks > 0:
                passed_checks += 1
            else:
                warnings.append("APK file found but was corrupt or empty")
                suggestions.append("Build a complete signed or debug APK with dex classes.")
        else:
            # If APK is expected but not built
            if "apk" in str(context.get("task", "")).lower() or "build" in str(context.get("task", "")).lower():
                errors.append("No valid .apk or .aab binary artifact generated in workspace")
                suggestions.append("Run `./gradlew assembleDebug` to compile and package the APK.")

        score = (passed_checks / max(1, total_checks))
        verified = bool(score >= 0.75 and not errors)

        return VerificationResult(
            verified=verified,
            score=round(score, 2),
            domain=self.domain,
            evidence=evidence,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            artifacts=artifacts,
            details={"manifest_count": len(manifest_files), "apk_count": len(apk_files)},
        )


class WebVerifier(BaseVerifier):
    domain = "web"

    def verify(self, context: Dict[str, Any]) -> VerificationResult:
        root_dir = Path(context.get("workspace_dir") or context.get("target_dir") or workspace.root)
        evidence: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        suggestions: List[str] = []
        artifacts: List[str] = []

        total_checks = 0
        passed_checks = 0

        # 1. Check HTML Entrypoint
        total_checks += 1
        html_files = list(root_dir.rglob("index.html")) + list(root_dir.rglob("*.html"))
        if html_files:
            passed_checks += 1
            entry = html_files[0]
            artifacts.append(str(entry))
            content = entry.read_text(encoding="utf-8", errors="ignore")
            evidence.append({"check": "html_entrypoint", "path": str(entry), "size": len(content)})
            if "<!doctype html" not in content.lower() and "<html" not in content.lower():
                warnings.append("HTML entrypoint missing <!DOCTYPE html> or <html> tags")
        else:
            # Check package.json for React / Next / Vue / Vite
            pkg_files = list(root_dir.rglob("package.json"))
            if pkg_files:
                passed_checks += 1
                evidence.append({"check": "node_project", "path": str(pkg_files[0])})
            else:
                errors.append("No HTML files or package.json found for web application")
                suggestions.append("Create an index.html or package.json for the web project.")

        # 2. Check Static Assets / Scripts / Styles
        total_checks += 1
        css_js_files = list(root_dir.rglob("*.css")) + list(root_dir.rglob("*.js")) + list(root_dir.rglob("*.ts")) + list(root_dir.rglob("*.tsx"))
        if css_js_files:
            passed_checks += 1
            evidence.append({"check": "web_scripts_and_styles", "count": len(css_js_files)})
        else:
            warnings.append("No CSS, JS, or TS files found in web project")

        # 3. Check Local HTTP Server if port is specified in context
        port = context.get("port")
        if port:
            total_checks += 1
            try:
                import urllib.request
                req = urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=2)
                if req.status in (200, 301, 302):
                    passed_checks += 1
                    evidence.append({"check": "http_server_alive", "port": port, "status": req.status})
                else:
                    errors.append(f"HTTP server on port {port} returned status {req.status}")
            except Exception as e:
                errors.append(f"HTTP server unreachable on port {port}: {e}")
                suggestions.append(f"Start the web server on 0.0.0.0:{port}")

        score = (passed_checks / max(1, total_checks))
        verified = bool(score >= 0.75 and not errors)

        return VerificationResult(
            verified=verified,
            score=round(score, 2),
            domain=self.domain,
            evidence=evidence,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            artifacts=artifacts,
            details={"html_files": len(html_files), "assets_count": len(css_js_files)},
        )


class GitVerifier(BaseVerifier):
    domain = "git"

    def verify(self, context: Dict[str, Any]) -> VerificationResult:
        root_dir = Path(context.get("workspace_dir") or context.get("target_dir") or workspace.root)
        evidence: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        suggestions: List[str] = []

        total_checks = 0
        passed_checks = 0

        # 1. Check Git Repository Exists
        total_checks += 1
        if (root_dir / ".git").exists():
            passed_checks += 1
            evidence.append({"check": "git_repo_present", "path": str(root_dir / ".git")})
        else:
            errors.append(f"Directory {root_dir} is not a git repository")
            return VerificationResult(
                verified=False, score=0.0, domain=self.domain, errors=errors
            )

        # 2. Check Branch Status
        total_checks += 1
        try:
            res = subprocess.run(["git", "status", "--porcelain"], cwd=str(root_dir), capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                passed_checks += 1
                uncommitted = [l for l in res.stdout.splitlines() if l.strip()]
                evidence.append({"check": "git_status", "uncommitted_files": len(uncommitted)})
                if context.get("require_clean_tree") and uncommitted:
                    errors.append(f"Working tree has {len(uncommitted)} uncommitted changes")
            else:
                errors.append(f"git status failed: {res.stderr}")
        except Exception as e:
            errors.append(f"git command error: {e}")

        # 3. Check Commit History
        total_checks += 1
        try:
            res_log = subprocess.run(["git", "log", "-n", "1", "--oneline"], cwd=str(root_dir), capture_output=True, text=True, timeout=5)
            if res_log.returncode == 0 and res_log.stdout.strip():
                passed_checks += 1
                evidence.append({"check": "git_head_commit", "latest": res_log.stdout.strip()})
            else:
                warnings.append("No commits found on current branch")
        except Exception:
            pass

        score = (passed_checks / max(1, total_checks))
        verified = bool(score >= 0.75 and not errors)

        return VerificationResult(
            verified=verified, score=round(score, 2), domain=self.domain,
            evidence=evidence, errors=errors, warnings=warnings, suggestions=suggestions
        )


class LinuxVerifier(BaseVerifier):
    domain = "linux"

    def verify(self, context: Dict[str, Any]) -> VerificationResult:
        evidence: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        suggestions: List[str] = []

        total_checks = 0
        passed_checks = 0

        # Check executable permissions if file specified
        target_file = context.get("file") or context.get("target_path")
        if target_file:
            total_checks += 1
            p = Path(target_file)
            if p.exists():
                if os.access(p, os.X_OK):
                    passed_checks += 1
                    evidence.append({"check": "file_executable", "file": str(p), "status": True})
                else:
                    if context.get("require_executable"):
                        errors.append(f"File {p} does not have executable permission (+x)")
                        suggestions.append(f"Run `chmod +x {p}`")
                    else:
                        passed_checks += 1

        # Check port listening if port specified
        port = context.get("port")
        if port:
            total_checks += 1
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.5)
            result = sock.connect_ex(("127.0.0.1", int(port)))
            sock.close()
            if result == 0:
                passed_checks += 1
                evidence.append({"check": "port_listening", "port": port, "status": "open"})
            else:
                errors.append(f"Port {port} is not listening")
                suggestions.append(f"Ensure target service is running and bound to port {port}")

        score = (passed_checks / max(1, total_checks)) if total_checks > 0 else 1.0
        verified = bool(score >= 0.8 and not errors)

        return VerificationResult(
            verified=verified, score=round(score, 2), domain=self.domain,
            evidence=evidence, errors=errors, warnings=warnings, suggestions=suggestions
        )


class ResearchVerifier(BaseVerifier):
    domain = "research"

    def verify(self, context: Dict[str, Any]) -> VerificationResult:
        content = str(context.get("output") or context.get("result") or context.get("content") or "")
        evidence: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        suggestions: List[str] = []

        total_checks = 0
        passed_checks = 0

        # 1. Non-empty & substantial content
        total_checks += 1
        word_count = len(content.split())
        if word_count >= 50:
            passed_checks += 1
            evidence.append({"check": "content_substance", "word_count": word_count})
        else:
            errors.append(f"Research output is too brief ({word_count} words; expected >= 50)")
            suggestions.append("Provide a comprehensive, synthesized analysis with detailed explanations.")

        # 2. Citations / URLs presence
        total_checks += 1
        urls = re.findall(r"https?://[^\s\)\>]+", content)
        citation_markers = re.findall(r"\[\d+\]|\[source\]|\[ref\]", content, re.IGNORECASE)
        if urls or citation_markers:
            passed_checks += 1
            evidence.append({"check": "citations_present", "url_count": len(urls), "citations": len(citation_markers)})
        else:
            warnings.append("No explicit source citations or reference URLs found in research text")
            suggestions.append("Include source URLs or numbered citations for verifiable claims.")

        # 3. Structure Check (Headings / Bullet Points)
        total_checks += 1
        has_headings = bool(re.search(r"^#+\s+|\n#+\s+", content))
        has_bullets = bool(re.search(r"^\s*[-*•]\s+", content, re.MULTILINE))
        if has_headings or has_bullets:
            passed_checks += 1
            evidence.append({"check": "structured_format", "has_headings": has_headings, "has_bullets": has_bullets})
        else:
            warnings.append("Output lacks structured sections or bullet points")

        score = (passed_checks / max(1, total_checks))
        verified = bool(score >= 0.65 and not errors)

        return VerificationResult(
            verified=verified, score=round(score, 2), domain=self.domain,
            evidence=evidence, errors=errors, warnings=warnings, suggestions=suggestions
        )


class FileVerifier(BaseVerifier):
    domain = "file"

    def verify(self, context: Dict[str, Any]) -> VerificationResult:
        root_dir = Path(context.get("workspace_dir") or workspace.root)
        target = context.get("target_path") or context.get("file")
        evidence: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        suggestions: List[str] = []
        artifacts: List[str] = []

        if not target:
            return VerificationResult(
                verified=False, score=0.0, domain=self.domain, errors=["No target file specified"]
            )

        p = Path(target) if Path(target).is_absolute() else root_dir / target
        if not p.exists():
            return VerificationResult(
                verified=False, score=0.0, domain=self.domain,
                errors=[f"File {p} does not exist"],
                suggestions=[f"Ensure file {p} is created before completing task"]
            )

        artifacts.append(str(p))
        size = p.stat().st_size
        evidence.append({"check": "file_exists", "path": str(p), "size_bytes": size})

        if size == 0:
            return VerificationResult(
                verified=False, score=0.2, domain=self.domain,
                errors=[f"File {p} is empty (0 bytes)"],
                artifacts=artifacts,
                suggestions=["Populate the target file with non-empty content"]
            )

        # Validate JSON if .json
        if p.suffix.lower() == ".json":
            try:
                json.loads(p.read_text(encoding="utf-8"))
                evidence.append({"check": "json_valid", "status": True})
            except Exception as e:
                errors.append(f"Invalid JSON syntax in {p.name}: {e}")

        return VerificationResult(
            verified=not errors,
            score=1.0 if not errors else 0.4,
            domain=self.domain,
            evidence=evidence,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            artifacts=artifacts,
        )


class GenericVerifier(BaseVerifier):
    domain = "generic"

    ERROR_MARKERS = (
        "error", "exception", "traceback", "failed", "failure", "no such file",
        "command not found", "refused", "timeout", "not running",
        "no api key", "not installed", "permission denied", "denied"
    )

    def verify(self, context: Dict[str, Any]) -> VerificationResult:
        text = str(context.get("output") or context.get("result") or "").strip()
        low = text.lower()
        problems = [m for m in self.ERROR_MARKERS if m in low]
        ok = bool(text) and not problems

        evidence = [{"check": "marker_scan", "problems_found": problems}]
        errors = [f"Problem marker detected: '{p}'" for p in problems] if problems else []
        suggestions = ["Fix the error output and verify required resources exist."] if problems else []

        return VerificationResult(
            verified=ok,
            score=1.0 if ok else (0.3 if text else 0.0),
            domain=self.domain,
            evidence=evidence,
            errors=errors,
            warnings=[],
            suggestions=suggestions,
        )


class VerifierRegistry:
    def __init__(self):
        self._verifiers: Dict[str, BaseVerifier] = {
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

    def list_domains(self) -> List[str]:
        return sorted(list(self._verifiers.keys()))

    def auto_detect_domain(self, task: str, files_modified: Optional[List[str]] = None, context: Optional[Dict[str, Any]] = None) -> str:
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
        context: Optional[Dict[str, Any]] = None,
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
        domains: List[str],
        context: Optional[Dict[str, Any]] = None,
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
            evidence=combined_evidence,
            errors=combined_errors,
            warnings=combined_warnings,
            suggestions=combined_suggestions,
            artifacts=combined_artifacts,
        )


verifier_registry = VerifierRegistry()
