"""Unit tests for Domain Verifiers, Software Engineer Mode, Model Router, and Skill Reliability."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from core.router2 import ModelRouter
from core.skill_manager import SkillManager
from core.swe_mode import SWEPhase, SoftwareEngineerMode, detect_toolchain
from core.verifier_registry import (
    AndroidVerifier,
    GitVerifier,
    LinuxVerifier,
    PythonVerifier,
    ResearchVerifier,
    WebVerifier,
    verifier_registry,
)


def test_python_verifier_ast_and_clean_run(tmp_path):
    py_file = tmp_path / "valid.py"
    py_file.write_text("""def greet(name: str) -> str:
    return f"Hello, {name}!"
""")

    pv = PythonVerifier()
    res = pv.verify({
        "task": "Write python greeting",
        "workspace_dir": str(tmp_path),
        "files_modified": [str(py_file)],
        "output": "Process exited with 0",
    })

    assert res.verified is True
    assert res.score >= 0.8
    assert len(res.errors) == 0


def test_python_verifier_detects_traceback(tmp_path):
    pv = PythonVerifier()
    res = pv.verify({
        "task": "Run script",
        "workspace_dir": str(tmp_path),
        "output": "Traceback (most recent call last):\n  File 'app.py', line 1\nModuleNotFoundError: No module named 'unknown_pkg'",
    })

    assert res.verified is False
    assert len(res.errors) >= 1
    assert any("Runtime exceptions detected" in e for e in res.errors)


def test_android_verifier_structure_and_artifacts(tmp_path):
    app_dir = tmp_path / "app" / "src" / "main"
    app_dir.mkdir(parents=True)
    manifest = app_dir / "AndroidManifest.xml"
    manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.app"></manifest>')

    gradle = tmp_path / "build.gradle"
    gradle.write_text('plugins { id "com.android.application" }')

    av = AndroidVerifier()
    res = av.verify({
        "task": "Create Android chat app",
        "workspace_dir": str(tmp_path),
    })

    assert res.score >= 0.6
    assert any(e["check"] == "manifest_exists" for e in res.evidence)


def test_web_verifier_html_and_assets(tmp_path):
    index = tmp_path / "index.html"
    index.write_text("<!DOCTYPE html><html><head><title>App</title></head><body>Hello</body></html>")
    css = tmp_path / "styles.css"
    css.write_text("body { background: #000; }")

    wv = WebVerifier()
    res = wv.verify({
        "task": "Create landing page",
        "workspace_dir": str(tmp_path),
    })

    assert res.verified is True
    assert res.score >= 0.8
    assert any(e["check"] == "html_entrypoint" for e in res.evidence)


def test_research_verifier_substance_and_citations():
    rv = ResearchVerifier()
    good_content = """# Autonomous Agents Overview
Autonomous agents leverage LLM reasoning to decompose high-level objectives into sequential steps.
Key frameworks evaluate task completion using domain-specific ground truths.

- Multi-Agent Orchestration: Enables specialized division of labor [1](https://example.com/agents).
- Formal Verification: Proves artifacts exist and tests pass [2](https://arxiv.org/abs/2301.00000).
- Checkpointing: Guarantees transactional rollback upon failed experiments.
"""

    res = rv.verify({"task": "Research agent architecture", "output": good_content})
    assert res.verified is True
    assert res.score >= 0.8
    assert any(e["check"] == "citations_present" for e in res.evidence)

    # Too short output fails
    res_short = rv.verify({"task": "Research agent architecture", "output": "Agents are cool."})
    assert res_short.verified is False


def test_swe_toolchain_detection(tmp_path):
    # Test Node/TypeScript
    (tmp_path / "package.json").write_text('{"name": "test-app", "dependencies": {"react": "^18.0.0"}, "scripts": {"build": "vite build"}}')
    (tmp_path / "tsconfig.json").write_text('{}')

    info = detect_toolchain(tmp_path)
    assert info.language == "typescript"
    assert info.framework == "react"
    assert info.build_tool == "npm run build"


def test_swe_mode_execution_and_reporting(tmp_path):
    ws = tmp_path / "swe_project"
    ws.mkdir()
    (ws / "math_lib.py").write_text("def add(a, b): return a + b\n")
    (ws / "test_math.py").write_text("from math_lib import add\ndef test_add(): assert add(2, 3) == 5\n")

    swe = SoftwareEngineerMode(workspace_root=ws)

    def mock_coder(prompt, ctx):
        return {
            "math_lib.py": "def add(a, b):\n    '''Add two numbers.'''\n    return a + b\n",
        }

    res = swe.execute("Refactor math_lib with docstring", workspace_dir=ws, coder_fn=mock_coder)
    assert res.success is True
    assert "math_lib.py" in res.files_modified
    assert "SWE Change Report" in res.change_report
    assert res.checkpoint_id is not None


def test_task_aware_model_router_specialized_roles():
    router = ModelRouter()
    # Code classification
    res_code = router.select("Write a Python FastAPI service with SQL endpoints")
    assert res_code["task_type"] == "code"

    # Vision classification
    res_vision = router.select("Analyze this screenshot and OCR the text")
    assert res_vision["task_type"] == "vision"

    # Critic role selection
    res_critic = router.select_for_role("critic", "Review security of auth.py")
    assert res_critic["task_type"] == "critic"


def test_skill_manager_regression_testing(tmp_path):
    sm = SkillManager(skills_dir=str(tmp_path / "skills"))
    skill_dir = tmp_path / "skills" / "json_parser"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# json_parser\nParses JSON strings safely.")
    (skill_dir / "skill.py").write_text("""import json
CAPABILITIES = ['read']
def run(task='', text='{}', **kwargs):
    return {'parsed': json.loads(text if text else '{}')}
""")
    (skill_dir / "test_skill.py").write_text("""from pathlib import Path
import importlib.util

def test_json_parser():
    spec = importlib.util.spec_from_file_location("skills.json_parser.skill", Path(__file__).parent / "skill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = mod.run(text='{"status": "ok"}')
    assert res['parsed']['status'] == 'ok'
""")

    # Run regression tests
    report = sm.run_regression_tests("json_parser")
    assert report["success"] is True
    assert report["passed"] == 1
    assert report["failed"] == 0

    # Log outcome and check health
    sm.log_skill_usage("json_parser", success=True, duration_ms=12.5, verification_score=1.0)
    health = sm.get_skill_health("json_parser")
    assert health["healthy"] is True
    assert health["reliability_score"] == 1.0
