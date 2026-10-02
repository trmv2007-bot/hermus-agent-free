"""Phase 8 scheduler: durable, timezone-aware one-shot and recurring tasks.

The scheduler owns when work should be submitted. It does not execute tools
itself: scheduled work is handed to the canonical JobQueue so approvals, red
lines, sandboxing, verification and runtime observability remain authoritative.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:  # pragma: no cover
    BackgroundScheduler = CronTrigger = DateTrigger = None
    APSCHEDULER_AVAILABLE = False

from core.config import config
from core.log import get_logger

logger = get_logger(__name__)

_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)
_IN_RE = re.compile(r"^in\s+(\d+)\s+(minute|minutes|hour|hours|day|days)\s*$", re.I)
_CRON_RE = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)$")


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_time(text: str) -> tuple[int, int] | None:
    match = _TIME_RE.search(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = (match.group(3) or "").lower()
    if minute > 59:
        return None
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour > 23:
        return None
    return hour, minute


def _recurring_cron(text: str) -> str | None:
    low = re.sub(r"\s+", " ", text.strip().lower())
    raw = low.removeprefix("cron:").strip()
    if _CRON_RE.fullmatch(raw):
        return raw
    if low in {"every minute", "every minute on the minute"}:
        return "* * * * *"
    match = re.fullmatch(r"every (\d+) minutes?", low)
    if match:
        return f"*/{max(1, int(match.group(1)))} * * * *"
    if low in {"every hour", "hourly"}:
        return "0 * * * *"
    match = re.fullmatch(r"every (\d+) hours?", low)
    if match:
        return f"0 */{max(1, int(match.group(1)))} * * *"
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    clock = _parse_time(low)
    if "every weekday" in low:
        hour, minute = clock or (9, 0)
        return f"{minute} {hour} * * 0-4"
    for day, dow in weekdays.items():
        if f"every {day}" in low:
            hour, minute = clock or (9, 0)
            return f"{minute} {hour} * * {dow}"
    if any(x in low for x in ("every day", "daily", "each day")):
        hour, minute = clock or (9, 0)
        return f"{minute} {hour} * * *"
    return None


def _one_shot(text: str, now: datetime) -> datetime | None:
    low = re.sub(r"\s+", " ", text.strip().lower())
    match = _IN_RE.match(low)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("minute"):
            return now + timedelta(minutes=amount)
        if unit.startswith("hour"):
            return now + timedelta(hours=amount)
        return now + timedelta(days=amount)
    clock = _parse_time(low)
    if "tomorrow" in low:
        target = now + timedelta(days=1)
        hour, minute = clock or (9, 0)
        return target.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if clock and not any(x in low for x in ("every ", "daily", "each ")):
        hour, minute = clock
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    return None


class CronManager:
    """Durable scheduler compatible with the existing cron CLI."""

    def __init__(self, db_path: str | None = None, *, timezone: str | None = None, start: bool = True):
        self.db_path = Path(db_path or config.resolve_path("data/cron_jobs.json"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.timezone = str(timezone or getattr(config, "timezone", "") or "UTC")
        try:
            self.tz = ZoneInfo(self.timezone)
        except Exception:
            self.timezone = "UTC"
            self.tz = ZoneInfo("UTC")
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self.scheduler = None
        self._load()
        if APSCHEDULER_AVAILABLE and start:
            try:
                self.scheduler = BackgroundScheduler(timezone=self.tz)
                self.scheduler.start()
                self._restore_enabled_jobs()
            except Exception as exc:
                logger.error(f"[Scheduler] failed to start: {exc}")
                self.scheduler = None

    def parse(self, text: str, *, now: datetime | None = None) -> dict[str, Any]:
        now = (now or _now()).astimezone(self.tz)
        cron = _recurring_cron(text)
        if cron:
            return {"schedule_type": "cron", "cron": cron, "run_at": None}
        run_at = _one_shot(text, now)
        if run_at:
            return {"schedule_type": "date", "cron": None, "run_at": run_at.astimezone(self.tz).isoformat()}
        return {"schedule_type": "cron", "cron": "0 9 * * *", "run_at": None}

    def _parse_natural_language(self, text: str) -> str:
        return str(self.parse(text).get("cron") or "0 9 * * *")

    def add_job(
        self,
        natural_text: str,
        task: str | None = None,
        platform: str = "cli",
        user_id: str = "default",
        *,
        timezone: str | None = None,
        enabled: bool = True,
        priority: str = "normal",
        max_runs: int | None = None,
        respect_quiet_hours: bool = False,
        quiet_hours: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        tz_name = str(timezone or self.timezone)
        try:
            ZoneInfo(tz_name)
        except Exception:
            raise ValueError(f"invalid timezone: {tz_name}")
        parsed = self.parse(natural_text, now=datetime.now(ZoneInfo(tz_name)))
        task_text = str(task or natural_text).strip()
        if not task_text:
            raise ValueError("task is required")
        if max_runs is not None and int(max_runs) < 1:
            raise ValueError("max_runs must be >= 1")
        job = {
            "id": f"cron_{uuid4().hex[:14]}",
            "natural": str(natural_text).strip(),
            "schedule_type": parsed["schedule_type"],
            "cron": parsed.get("cron"),
            "run_at": parsed.get("run_at"),
            "task": task_text,
            "platform": str(platform or "cli"),
            "user_id": str(user_id or "default"),
            "timezone": tz_name,
            "priority": str(priority or "normal"),
            "enabled": bool(enabled),
            "max_runs": int(max_runs) if max_runs is not None else None,
            "run_count": 0,
            "last_run_at": None,
            "last_error": None,
            "last_job_id": None,
            "last_run_id": None,
            "respect_quiet_hours": bool(respect_quiet_hours),
            "quiet_hours": list(quiet_hours) if quiet_hours else None,
            "created": datetime.now(ZoneInfo(tz_name)).isoformat(),
            "next_run_at": None,
        }
        with self._lock:
            self._jobs[job["id"]] = job
            self._save()
            self._schedule(job)
        return dict(job)

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            job["enabled"] = bool(enabled)
            if job["enabled"]:
                self._schedule(job)
            elif self.scheduler:
                try:
                    self.scheduler.remove_job(job_id)
                except Exception:
                    pass
            self._save()
            return True

    def remove_job(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                return False
            del self._jobs[job_id]
            if self.scheduler:
                try:
                    self.scheduler.remove_job(job_id)
                except Exception:
                    pass
            self._save()
            return True

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(job) for job in self._jobs.values()]

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": APSCHEDULER_AVAILABLE,
                "running": bool(self.scheduler),
                "timezone": self.timezone,
                "jobs": len(self._jobs),
                "enabled_jobs": sum(1 for j in self._jobs.values() if j.get("enabled")),
            }

    def _in_quiet_hours(self, job: dict[str, Any], when: datetime) -> bool:
        window = job.get("quiet_hours")
        if not job.get("respect_quiet_hours") or not window:
            return False
        start, end = int(window[0]), int(window[1])
        return (start <= when.hour < end) if start <= end else (when.hour >= start or when.hour < end)

    def _execute_job(self, job: dict[str, Any]) -> None:
        with self._lock:
            current = self._jobs.get(job["id"])
            if current is None or not current.get("enabled"):
                return
            if current.get("max_runs") is not None and current.get("run_count", 0) >= int(current["max_runs"]):
                current["enabled"] = False
                self._save()
                return
            tz = ZoneInfo(current.get("timezone") or self.timezone)
            now = datetime.now(tz)
            if self._in_quiet_hours(current, now):
                current["last_error"] = "deferred_by_quiet_hours"
                if current.get("schedule_type") == "date" and current.get("quiet_hours"):
                    start, end = int(current["quiet_hours"][0]), int(current["quiet_hours"][1])
                    target = now.replace(hour=end, minute=0, second=0, microsecond=0)
                    if start <= end:
                        if target <= now:
                            target += timedelta(days=1)
                    elif now.hour >= start:
                        target += timedelta(days=1)
                    current["run_at"] = target.isoformat()
                self._save()
                if current.get("schedule_type") == "date":
                    self._schedule(current)
                return
        try:
            from gateway.queue import job_queue
            payload = {
                "text": current["task"],
                "task": current["task"],
                "platform": current["platform"],
                "user_id": current["user_id"],
                "schedule_id": current["id"],
                "scheduled": True,
                "priority": current["priority"],
                "trigger": {"type": "schedule", "schedule_id": current["id"], "natural": current["natural"]},
            }
            queued = job_queue.submit("runtime.turn", payload, session_key=f"schedule:{current['user_id']}")
            with self._lock:
                latest = self._jobs.get(current["id"])
                if latest:
                    latest["run_count"] = int(latest.get("run_count", 0)) + 1
                    latest["last_run_at"] = now.isoformat()
                    latest["last_error"] = None
                    latest["last_job_id"] = queued.id
                    latest["last_run_id"] = queued.run_id
                    if latest.get("schedule_type") == "date" or (
                        latest.get("max_runs") is not None and latest["run_count"] >= int(latest["max_runs"])
                    ):
                        latest["enabled"] = False
                    self._save()
                    if not latest["enabled"] and self.scheduler:
                        try:
                            self.scheduler.remove_job(latest["id"])
                        except Exception:
                            pass
        except Exception as exc:
            with self._lock:
                latest = self._jobs.get(current["id"])
                if latest:
                    latest["last_error"] = str(exc)[:300]
                    self._save()
            logger.error(f"[Scheduler] enqueue failed for {current['id']}: {exc}")

    def _schedule(self, job: dict[str, Any]) -> None:
        if not self.scheduler or not job.get("enabled"):
            return
        try:
            try:
                self.scheduler.remove_job(job["id"])
            except Exception:
                pass
            tz = ZoneInfo(job.get("timezone") or self.timezone)
            if job.get("schedule_type") == "date":
                trigger = DateTrigger(run_date=datetime.fromisoformat(job["run_at"]).astimezone(tz), timezone=tz)
            else:
                parts = str(job.get("cron") or "0 9 * * *").split()
                if len(parts) != 5:
                    raise ValueError("cron must contain five fields")
                trigger = CronTrigger(
                    minute=parts[0], hour=parts[1], day=parts[2], month=parts[3],
                    day_of_week=parts[4], timezone=tz,
                )
            aps_job = self.scheduler.add_job(
                self._execute_job,
                trigger=trigger,
                args=[job],
                id=job["id"],
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=120,
            )
            next_run = getattr(aps_job, "next_run_time", None)
            job["next_run_at"] = next_run.isoformat() if next_run else None
            self._save()
        except Exception as exc:
            job["last_error"] = f"schedule_error: {exc}"[:300]
            self._save()
            logger.error(f"[Scheduler] failed to schedule {job.get('id')}: {exc}")

    def _restore_enabled_jobs(self) -> None:
        for job in list(self._jobs.values()):
            if job.get("enabled"):
                self._schedule(job)

    def _load(self) -> None:
        try:
            rows = json.loads(self.db_path.read_text())
        except (OSError, ValueError):
            rows = []
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id") or not row.get("task"):
                continue
            row.setdefault("schedule_type", "cron")
            row.setdefault("cron", "0 9 * * *")
            row.setdefault("run_at", None)
            row.setdefault("timezone", self.timezone)
            row.setdefault("enabled", True)
            row.setdefault("priority", "normal")
            row.setdefault("max_runs", None)
            row.setdefault("run_count", 0)
            row.setdefault("last_run_at", None)
            row.setdefault("last_error", None)
            row.setdefault("last_job_id", None)
            row.setdefault("last_run_id", None)
            row.setdefault("respect_quiet_hours", False)
            row.setdefault("quiet_hours", None)
            row.setdefault("created", _now().isoformat())
            row.setdefault("next_run_at", None)
            self._jobs[str(row["id"])] = row

    def _save(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.db_path.with_suffix(self.db_path.suffix + ".tmp")
            tmp.write_text(json.dumps(list(self._jobs.values()), indent=2, default=str))
            tmp.replace(self.db_path)
        except OSError:
            pass


cron_manager = CronManager()

__all__ = ["CronManager", "cron_manager", "APSCHEDULER_AVAILABLE"]
