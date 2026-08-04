"""Scheduler - Built-in cron with natural language, free APScheduler"""
import re
from datetime import datetime
from typing import List, Dict
from pathlib import Path
import json

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False

from core.config import config
from core.agent import HermusAgent

class CronManager:
    """Free cron scheduler with natural language parsing"""

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or config.resolve_path("data/cron_jobs.json"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self.db_path.write_text("[]")
        self.scheduler = None
        if APSCHEDULER_AVAILABLE:
            self.scheduler = BackgroundScheduler()
            self.scheduler.start()

    def _parse_natural_language(self, text: str) -> str:
        """Parse natural language to cron via simple rules + LLM fallback - free"""
        text_lower = text.lower()

        # Simple rule-based parser for common patterns - free, no paid API
        if "every day at 9am" in text_lower or "daily at 9am" in text_lower or "daily 9am" in text_lower:
            return "0 9 * * *"
        if "every day at 8am" in text_lower:
            return "0 8 * * *"
        if "every monday" in text_lower and "8am" in text_lower:
            return "0 8 * * 1"
        if "every hour" in text_lower:
            return "0 * * * *"
        if "every minute" in text_lower:
            return "* * * * *"

        # Try extract time like "at 9am" or "at 14:30"
        time_match = re.search(r'at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text_lower)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            ampm = time_match.group(3)
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            return f"{minute} {hour} * * *"

        # Fallback: try LLM to parse to cron (free via Ollama)
        try:
            from core.llm import free_llm
            messages = [
                {"role": "system", "content": "Convert natural language schedule to cron expression. Only return cron, no explanation. Example: 'daily at 9am' -> '0 9 * * *'"},
                {"role": "user", "content": text}
            ]
            resp = free_llm.chat(messages)
            # Extract cron-like pattern
            cron_match = re.search(r'(\d+|\*)\s+(\d+|\*)\s+(\d+|\*)\s+(\d+|\*)\s+(\d+|\*)', resp.content)
            if cron_match:
                return cron_match.group(0)
        except:
            pass

        # Default daily 9am
        return "0 9 * * *"

    def add_job(self, natural_text: str, task: str = None, platform: str = "cli", user_id: str = "default") -> Dict:
        """Add cron job from natural language"""
        cron_expr = self._parse_natural_language(natural_text)
        # If task not provided, use natural_text as task
        task_text = task or natural_text

        job = {
            "id": f"cron_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "natural": natural_text,
            "cron": cron_expr,
            "task": task_text,
            "platform": platform,
            "user_id": user_id,
            "created": datetime.now().isoformat(),
            "enabled": True
        }

        # Save to file
        try:
            jobs = json.loads(self.db_path.read_text())
        except:
            jobs = []
        jobs.append(job)
        self.db_path.write_text(json.dumps(jobs, indent=2))

        # Schedule via APScheduler if available
        if self.scheduler:
            try:
                # Parse cron 5 fields
                minute, hour, day, month, dow = cron_expr.split()
                self.scheduler.add_job(
                    self._execute_job,
                    'cron',
                    minute=minute,
                    hour=hour,
                    day=day,
                    month=month,
                    day_of_week=dow,
                    args=[job],
                    id=job["id"]
                )
            except Exception as e:
                print(f"APScheduler failed to add job: {e} - but saved to file")

        return job

    def _execute_job(self, job: Dict):
        """Execute cron job - delivers to platform"""
        print(f"[Cron] Executing job {job['id']}: {job['task']} -> {job['platform']}:{job['user_id']}")
        try:
            # Use agent to execute task
            agent = HermusAgent(session_id=f"cron_{job['id']}")
            result = agent.chat(job["task"])
            # In free version, delivery is via gateway - for now just log
            # Real gateway would send via Telegram/Discord API
            print(f"[Cron] Result for {job['platform']}:{job['user_id']}: {result['response'][:200]}")

            # If platform is telegram/discord and token set, could send via API here (free)
        except Exception as e:
            print(f"[Cron] Job {job['id']} failed: {e}")

    def list_jobs(self) -> List[Dict]:
        try:
            return json.loads(self.db_path.read_text())
        except:
            return []

    def remove_job(self, job_id: str) -> bool:
        try:
            jobs = json.loads(self.db_path.read_text())
            jobs = [j for j in jobs if j["id"] != job_id]
            self.db_path.write_text(json.dumps(jobs, indent=2))
            if self.scheduler:
                try:
                    self.scheduler.remove_job(job_id)
                except:
                    pass
            return True
        except:
            return False

cron_manager = CronManager()
