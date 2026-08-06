"""Skill Manager - Autonomous skill creation + self-improvement, agentskills.io compatible, free - Optimized with caching"""
import json
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
from .config import config
from .cache import skill_cache

class SkillManager:
    """Free skill system: creates reusable skills from trajectories, self-improves on use - Optimized"""

    def __init__(self, skills_dir: str = None):
        self.skills_dir = Path(skills_dir or config.resolve_path(config.skills_dir))
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> List[Dict]:
        """List all skills - compatible with /skills command - Optimized with caching"""
        cache_key = skill_cache.make_key("list_skills")
        cached = skill_cache.get(cache_key)
        if cached is not None:
            return cached

        skills = []
        for skill_dir in self.skills_dir.iterdir():
            if skill_dir.is_dir():
                md_path = skill_dir / "SKILL.md"
                py_path = skill_dir / "skill.py"
                if md_path.exists():
                    try:
                        content = md_path.read_text()
                        skills.append({
                            "name": skill_dir.name,
                            "path": str(skill_dir),
                            "description": content[:200],
                            "has_code": py_path.exists()
                        })
                    except:
                        pass
        skill_cache.set(cache_key, skills)
        return skills

    def get_skill(self, name: str) -> Dict:
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
        result = {"name": name, "path": str(skill_dir)}
        if md_path.exists():
            result["doc"] = md_path.read_text()
        if py_path.exists():
            result["code"] = py_path.read_text()
        
        skill_cache.set(cache_key, result)
        return result

    def should_create_skill(self, trajectory: List[Dict]) -> bool:
        """Decide if trajectory warrants skill creation - 3+ tool calls = complex"""
        tool_calls = sum(1 for turn in trajectory for _ in (turn.get("tool_calls") or []))
        return tool_calls >= config.auto_skill_threshold

    def create_skill_from_trajectory(self, trajectory: List[Dict], session_id: str) -> Dict:
        """Autonomous skill creation after complex task - free via LLM"""
        if not self.should_create_skill(trajectory):
            return {"created": False, "reason": f"Only {len(trajectory)} tool calls, need >= {config.auto_skill_threshold}"}

        # Build trajectory summary for LLM
        traj_text = "\n".join([
            f"{turn.get('role')}: {turn.get('content','')[:300]} | Tools: {turn.get('tool_calls')}"
            for turn in trajectory[-10:]
        ])

        try:
            from .llm import free_llm
            messages = [
                {"role": "system", "content": "You are a skill creator. Given a successful trajectory of tool calls, create a reusable Python skill. Output JSON with name, description, and python code. Use agentskills.io format: SKILL.md with description + skill.py with function. Keep it simple and reusable."},
                {"role": "user", "content": f"Trajectory:\n{traj_text}\n\nCreate a skill. Return JSON: {{\"name\": \"skill_name_snake_case\", \"description\": \"...\", \"code\": \"def ...\"}}"}
            ]
            resp = free_llm.chat(messages)
            content = resp.content
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                try:
                    skill_data = json.loads(json_match.group(0))
                except:
                    skill_data = {
                        "name": f"auto_skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                        "description": f"Auto-created from session {session_id}: {traj_text[:200]}",
                        "code": f"# Auto skill from {session_id}\n# Trajectory: {traj_text[:500]}\n\ndef run():\n    print('Auto skill - implement based on trajectory')\n"
                    }
            else:
                skill_data = {
                    "name": f"auto_skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    "description": content[:500],
                    "code": "# Failed to parse, manual implementation needed\n"
                }
        except Exception as e:
            skill_data = {
                "name": f"auto_skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "description": f"Auto skill from {session_id} (LLM failed: {e})",
                "code": f"# Fallback skill\n# Original trajectory: {traj_text[:500]}\n\ndef run():\n    pass\n"
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

## Usage
```python
from skills.{name}.skill import run
run()
```

## When to use
This skill was auto-created after a complex task with {len(trajectory)} turns. Use when similar task appears.

## Trajectory (for training)
```
{traj_text[:2000]}
```
"""
        (skill_dir / "SKILL.md").write_text(md_content)
        code = skill_data.get("code", "# No code")
        if "def " not in code:
            code = f"def run():\n    {code}\n"
        (skill_dir / "skill.py").write_text(code)
        # Clear cache after creation
        skill_cache.clear()

        return {"created": True, "name": name, "path": str(skill_dir), "description": skill_data.get("description","")}

    def log_skill_usage(self, skill_name: str, success: bool, feedback: str = ""):
        """Log usage for self-improvement - free SQLite"""
        try:
            from .memory import memory
            import sqlite3
            db_path = memory.db_path
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO skill_usage (skill_name, timestamp, success, feedback)
                VALUES (?, ?, ?, ?)
            """, (skill_name, datetime.now().isoformat(), success, feedback))
            conn.commit()
            conn.close()
        except:
            pass

    def improve_skill(self, skill_name: str):
        """Self-improve skill during use - free via LLM"""
        skill = self.get_skill(skill_name)
        if not skill:
            return {"improved": False, "reason": "Skill not found"}

        try:
            from .memory import memory
            import sqlite3
            conn = sqlite3.connect(str(memory.db_path))
            cur = conn.cursor()
            cur.execute("SELECT * FROM skill_usage WHERE skill_name=? ORDER BY id DESC LIMIT 5", (skill_name,))
            usages = cur.fetchall()
            conn.close()

            success_count = sum(1 for u in usages if u[3])
            fail_count = len(usages) - success_count

            if len(usages) < 2:
                return {"improved": False, "reason": "Not enough usage to improve"}

            from .llm import free_llm
            usage_text = "\n".join([f"{'SUCCESS' if u[3] else 'FAIL'}: {u[4]}" for u in usages])
            messages = [
                {"role": "system", "content": "You are a skill improver. Given skill code and usage feedback (success/fail), improve the code to be more robust."},
                {"role": "user", "content": f"Skill: {skill_name}\nCode:\n{skill.get('code','')[:2000]}\n\nUsage:\n{usage_text}\n\nSuccess: {success_count}, Fail: {fail_count}\n\nImprove the code and return only improved Python code."}
            ]
            resp = free_llm.chat(messages)
            improved_code = resp.content
            code_match = re.search(r'```python\n(.*?)\n```', improved_code, re.DOTALL)
            if code_match:
                improved_code = code_match.group(1)
            else:
                code_match = re.search(r'```\n(.*?)\n```', improved_code, re.DOTALL)
                if code_match:
                    improved_code = code_match.group(1)

            skill_dir = Path(skill["path"])
            old_code = (skill_dir / "skill.py").read_text()
            (skill_dir / f"skill.py.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}").write_text(old_code)
            (skill_dir / "skill.py").write_text(improved_code)
            skill_cache.clear()

            return {"improved": True, "name": skill_name, "old_success": success_count, "new_code": improved_code[:500]}

        except Exception as e:
            return {"improved": False, "reason": f"Improve failed: {e}"}

skill_manager = SkillManager()
