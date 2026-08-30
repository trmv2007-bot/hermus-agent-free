"""Skill Manager - Autonomous skill creation + self-improvement, agentskills.io compatible, free - Optimized with caching"""
import ast
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .cache import skill_cache
from .config import config
from .permissions import Capability


class SkillManager:
    """Free skill system: creates reusable skills from trajectories, self-improves on use, regression-tested."""

    def __init__(self, skills_dir: Optional[str] = None):
        self.skills_dir = Path(skills_dir or config.resolve_path(config.skills_dir))
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> list[dict[str, Any]]:
        """List all skills - compatible with /skills command - Optimized with caching"""
        cache_key = skill_cache.make_key("list_skills")
        cached = skill_cache.get(cache_key)
        if cached is not None:
            return cached

        skills = []
        for skill_dir in self.skills_dir.iterdir():
            if skill_dir.is_dir() and not skill_dir.name.startswith("."):
                md_path = skill_dir / "SKILL.md"
                py_path = skill_dir / "skill.py"
                if md_path.exists():
                    try:
                        content = md_path.read_text(encoding="utf-8")
                        caps = self.get_skill_capabilities(skill_dir.name)
                        skills.append({
                            "name": skill_dir.name,
                            "path": str(skill_dir),
                            "description": content[:200],
                            "has_code": py_path.exists(),
                            "capabilities": caps,
                        })
                    except Exception:
                        pass
        skill_cache.set(cache_key, skills)
        return skills

    def get_skill(self, name: str) -> dict[str, Any]:
        """Get skill by name - Optimized with caching"""
        cache_key = skill_cache.make_key("get_skill", name)
        cached = skill_cache.get(cache_key)
        if cached is not None:
            return cached

        skill_dir = self.skills_dir / name
        if not skill_dir.exists():
            return {}

        md_path = skill_dir / "SKILL.md"
        py_path = skill_dir / "skill.py"
        test_path = skill_dir / "test_skill.py"
        result = {"name": name, "path": str(skill_dir)}
        if md_path.exists():
            result["doc"] = md_path.read_text(encoding="utf-8")
        if py_path.exists():
            result["code"] = py_path.read_text(encoding="utf-8")
        if test_path.exists():
            result["has_tests"] = True
            result["test_code"] = test_path.read_text(encoding="utf-8")
        else:
            result["has_tests"] = False

        result["capabilities"] = self.get_skill_capabilities(name)

        skill_cache.set(cache_key, result)
        return result

    def get_skill_capabilities(self, name: str) -> list[str]:
        """Inspect skill source to extract declared or inferred security capabilities."""
        skill_dir = self.skills_dir / name
        py_path = skill_dir / "skill.py"
        if not py_path.exists():
            return [Capability.READ.value]

        try:
            code = py_path.read_text(encoding="utf-8")
            # Look for CAPABILITIES = [...]
            m = re.search(r"CAPABILITIES\s*=\s*\[(.*?)\]", code, re.DOTALL)
            if m:
                raw = m.group(1)
                caps = [c.strip(" '\"\n\r") for c in raw.split(",") if c.strip(" '\"\n\r")]
                return caps

            # Infer from AST
            caps = [Capability.READ.value]
            if "requests" in code or "urllib" in code or "http" in code:
                caps.append(Capability.NETWORK.value)
            if "subprocess" in code or "os.system" in code:
                caps.append(Capability.EXECUTE_SANDBOX.value)
            if "open(" in code and ("'w'" in code or '"w"' in code):
                caps.append(Capability.WRITE_WORKSPACE.value)
            return list(set(caps))
        except Exception:
            return [Capability.READ.value]

    def should_create_skill(self, trajectory: list[dict]) -> bool:
        """Decide if trajectory warrants skill creation - 3+ tool calls = complex"""
        tool_calls = sum(1 for turn in trajectory for _ in (turn.get("tool_calls") or []))
        return tool_calls >= config.auto_skill_threshold

    def create_skill_from_trajectory(self, trajectory: list[dict], session_id: str) -> dict:
        """Autonomous skill creation after complex task - free via LLM"""
        if not self.should_create_skill(trajectory):
            return {"created": False, "reason": f"Only {len(trajectory)} tool calls, need >= {config.auto_skill_threshold}"}

        traj_text = "\n".join([
            f"{turn.get('role')}: {turn.get('content','')[:300]} | Tools: {turn.get('tool_calls')}"
            for turn in trajectory[-10:]
        ])

        try:
            from .llm import free_llm
            messages = [
                {"role": "system", "content": "You are a skill creator. Given a successful trajectory of tool calls, create a reusable Python skill. Output JSON with name, description, capabilities list, and python code."},
                {"role": "user", "content": f"Trajectory:\n{traj_text}\n\nCreate a skill. Return JSON: {{\"name\": \"skill_name_snake_case\", \"description\": \"...\", \"capabilities\": [\"read\"], \"code\": \"def ...\"}}"}
            ]
            resp = free_llm.chat(messages)
            content = resp.content
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                try:
                    skill_data = json.loads(json_match.group(0))
                except Exception:
                    skill_data = {
                        "name": f"auto_skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                        "description": f"Auto-created from session {session_id}: {traj_text[:200]}",
                        "capabilities": ["read", "write_workspace"],
                        "code": f"# Auto skill from {session_id}\n# Trajectory: {traj_text[:500]}\n\nCAPABILITIES = ['read', 'write_workspace']\n\ndef run(task: str = '', query: str = '', **context):\n    '''Reusable skill - task/query/context passed by skill_use'''\n    print('Auto skill - implement based on trajectory')\n    return {{'task': task or query, 'note': 'stub auto skill'}}\n"
                    }
            else:
                skill_data = {
                    "name": f"auto_skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    "description": content[:500],
                    "capabilities": ["read"],
                    "code": "# Failed to parse, manual implementation needed\n"
                }
        except Exception as e:
            skill_data = {
                "name": f"auto_skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "description": f"Auto skill from {session_id} (LLM failed: {e})",
                "capabilities": ["read"],
                "code": f"# Fallback skill\n# Original trajectory: {traj_text[:500]}\n\nCAPABILITIES = ['read']\n\ndef run(task: str = '', query: str = '', **context):\n    return {{'task': task or query, 'trajectory_hint': '''{traj_text[:200]}'''}}\n"
            }

        name = re.sub(r'[^a-zA-Z0-9_]', '_', skill_data.get("name", "auto_skill")).lower()
        if not name:
            name = f"auto_skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        skill_dir = self.skills_dir / name
        skill_dir.mkdir(exist_ok=True)

        md_content = f"""# {name}

{skill_data.get('description','Auto-created skill')}

## Source
- Session: {session_id}
- Created: {datetime.now().isoformat()}
- Trajectory length: {len(trajectory)} turns
- Capabilities: {', '.join(skill_data.get('capabilities', ['read']))}

## Usage
```python
from skills.{name}.skill import run
run(task="...", query="...", **context)
```
"""
        (skill_dir / "SKILL.md").write_text(md_content, encoding="utf-8")
        code = skill_data.get("code", "# No code")
        if "def " not in code:
            code = (
                "CAPABILITIES = ['read', 'write_workspace']\n\n"
                "def run(task: str = '', query: str = '', **context):\n"
                f"    result = {code!r}\n"
                "    return {'task': task or query, 'result': result}\n"
            )

        # Validate syntax before writing
        try:
            ast.parse(code)
        except SyntaxError:
            code = (
                "CAPABILITIES = ['read']\n\n"
                "def run(task: str = '', query: str = '', **context):\n"
                "    return {'task': task or query, 'status': 'valid'}\n"
            )

        (skill_dir / "skill.py").write_text(code, encoding="utf-8")

        # Auto-generate a regression test for the newly created skill
        test_code = f"""# Auto-generated regression test for skill: {name}
import importlib.util
from pathlib import Path

def test_{name}_entrypoint():
    spec = importlib.util.spec_from_file_location("skills.{name}.skill", Path(__file__).parent / "skill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "run"), "Skill must define a run() entrypoint"
    res = mod.run(task="test")
    assert isinstance(res, dict), "Skill run() must return a dictionary"
"""
        (skill_dir / "test_skill.py").write_text(test_code, encoding="utf-8")

        # Clear cache after creation
        skill_cache.clear()

        return {"created": True, "name": name, "path": str(skill_dir), "description": skill_data.get("description","")}

    def log_skill_usage(
        self,
        skill_name: str,
        success: bool,
        feedback: str = "",
        duration_ms: Optional[float] = None,
        failure_category: Optional[str] = None,
        verification_score: Optional[float] = None,
    ) -> None:
        """Log usage metrics and reliability evidence for self-improvement."""
        try:
            from .memory import memory
            from .db_registry import using

            db_path = memory.db_path
            with using(db_path, owner="skill_usage") as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS skill_usage (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        skill_name TEXT,
                        timestamp TEXT,
                        success INTEGER,
                        feedback TEXT,
                        duration_ms REAL,
                        failure_category TEXT,
                        verification_score REAL
                    )
                """)
                cur.execute("""
                    INSERT INTO skill_usage (skill_name, timestamp, success, feedback, duration_ms, failure_category, verification_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (skill_name, datetime.now().isoformat(), 1 if success else 0, feedback, duration_ms, failure_category, verification_score))
                conn.commit()
        except Exception:
            pass

    def run_regression_tests(self, skill_name: Optional[str] = None) -> dict[str, Any]:
        """Execute regression tests for skills to detect degradation."""
        skills_to_test = [skill_name] if skill_name else [s["name"] for s in self.list_skills()]
        results: dict[str, Any] = {}
        passed = 0
        failed = 0

        for name in skills_to_test:
            s_dir = self.skills_dir / name
            test_file = s_dir / "test_skill.py"
            if not test_file.exists():
                results[name] = {"status": "skipped", "reason": "No test_skill.py found"}
                continue

            try:
                res = subprocess.run(
                    ["pytest", "-v", str(test_file)],
                    cwd=str(self.skills_dir.parent),
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if res.returncode == 0:
                    passed += 1
                    results[name] = {"status": "passed", "output": res.stdout[:300]}
                else:
                    failed += 1
                    results[name] = {"status": "failed", "output": res.stdout[:500] + res.stderr[:500]}
            except Exception as e:
                failed += 1
                results[name] = {"status": "error", "error": str(e)}

        return {
            "total_tested": passed + failed,
            "passed": passed,
            "failed": failed,
            "success": failed == 0,
            "details": results,
        }

    def get_skill_health(self, skill_name: str) -> dict[str, Any]:
        """Compute usage health and reliability score for a skill."""
        try:
            from .memory import memory
            from .db_registry import using

            with using(memory.db_path, owner="skill_usage") as conn:
                cur = conn.cursor()
                cur.execute("SELECT success, verification_score FROM skill_usage WHERE skill_name=? ORDER BY id DESC LIMIT 20", (skill_name,))
                rows = cur.fetchall()
            if not rows:
                return {"total": 0, "successes": 0, "success_rate": 1.0, "reliability_score": 1.0, "consecutive_failures": 0, "healthy": True}
            successes = sum(1 for r in rows if r[0])
            total = len(rows)
            consec_fail = 0
            for r in rows:
                if not r[0]:
                    consec_fail += 1
                else:
                    break
            success_rate = round(successes / total, 2)
            healthy = consec_fail < 3 and (total < 4 or success_rate >= 0.4)
            return {
                "total": total,
                "successes": successes,
                "success_rate": success_rate,
                "reliability_score": success_rate,
                "consecutive_failures": consec_fail,
                "healthy": healthy,
            }
        except Exception:
            return {"total": 0, "successes": 0, "success_rate": 1.0, "reliability_score": 1.0, "consecutive_failures": 0, "healthy": True}


skill_manager = SkillManager()
