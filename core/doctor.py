"""Hermus Doctor — Hermus's own physician.

The doctor's customer is **Hermus, not the user**.  Its job is to notice that
something inside Hermus broke, work out *what* went wrong and *how to manage
it*, and hand back a report with concrete next steps.  Because it is meant to
run on a small local model (the accelerator router gives it the NPU when there
is one — see :mod:`core.accelerators`), the design is deliberately split:

1. **Deterministic triage first.**  :meth:`HermusDoctor.analyze` reads real
   runtime signals — structured runtime issues, stuck runs/jobs, install
   diagnostics, engine reachability, disk space — and emits findings with
   evidence and fixes.  This needs no model at all, so a broken LLM can never
   blind the doctor to a broken runtime.
2. **Small-model narrative second.**  :meth:`HermusDoctor.triage` asks the
   doctor engine to explain the findings in plain language and propose a
   management plan.  If the model is unreachable the deterministic report
   stands on its own.
3. **Ask the internet when stuck.**  :meth:`HermusDoctor.research` runs a
   bounded DuckDuckGo lookup for findings it has no built-in fix for, so a
   small model is not limited to what it already knows.  Only real results are
   reported — the offline mock search is detected and labelled instead of
   being passed off as research.

Nothing is left in a "processing" state: :meth:`HermusDoctor.find_stuck_work`
looks for runs/jobs that never reached a terminal status, and
:meth:`HermusDoctor.reap_stuck` can close them out with an explicit reason.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import config

SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
SEVERITY_INFO = "info"

_SEVERITY_ORDER = {
    SEVERITY_CRITICAL: 0,
    SEVERITY_HIGH: 1,
    SEVERITY_MEDIUM: 2,
    SEVERITY_LOW: 3,
    SEVERITY_INFO: 4,
}

# Statuses that mean "still working" — anything in them past the threshold is
# stuck work the doctor must surface.
IN_FLIGHT_JOB_STATUSES = ("queued", "running")
IN_FLIGHT_RUN_STATUSES = ("running",)


@dataclass
class Finding:
    """One diagnosed problem, with the evidence and the way out."""

    id: str
    severity: str
    category: str
    title: str
    evidence: str
    fixes: list[str] = field(default_factory=list)
    component: str = ""
    references: list[str] = field(default_factory=list)
    auto_fixable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "evidence": self.evidence,
            "fixes": list(self.fixes),
            "component": self.component,
            "references": list(self.references),
            "auto_fixable": self.auto_fixable,
        }


def _finding(
    severity: str,
    category: str,
    title: str,
    evidence: str,
    fixes: Optional[list[str]] = None,
    *,
    component: str = "",
    auto_fixable: bool = False,
) -> Finding:
    return Finding(
        id=f"{category}-{uuid.uuid4().hex[:8]}",
        severity=severity,
        category=category,
        title=title,
        evidence=evidence,
        fixes=list(fixes or []),
        component=component,
        auto_fixable=auto_fixable,
    )


class HermusDoctor:
    """Collect → diagnose → (optionally) ask a model and the internet → report."""

    def __init__(self, reports_dir: Optional[str] = None) -> None:
        self.reports_dir = Path(
            reports_dir or config.resolve_path(getattr(config, "doctor_reports_dir", "data/doctor"))
        )
        self._runs: list[dict[str, Any]] = []
        self._auto_last = 0.0
        self._auto_today = ""
        self._auto_count = 0

    # ------------------------------------------------------------------ signals
    def collect(self, *, stuck_minutes: Optional[int] = None) -> dict[str, Any]:
        """Gather every signal the doctor reasons over. Never raises."""
        signals: dict[str, Any] = {
            "collected_at": _now(),
            "issues": self._runtime_issues(),
            "stuck": self.find_stuck_work(stuck_minutes=stuck_minutes),
            "diagnostics": self._diagnostics(),
            "watchdog": self._watchdog_history(),
            "engines": self._engine_state(),
            "media": self._media_state(),
            "resources": self._resources(),
        }
        return signals

    @staticmethod
    def _runtime_issues(limit: int = 200) -> list[dict[str, Any]]:
        try:
            from .run_events import recent_issues

            return list(recent_issues(limit=limit))
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _diagnostics() -> dict[str, Any]:
        try:
            from .diagnostics import run_diagnostics

            return run_diagnostics()
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}", "checks": []}

    @staticmethod
    def _watchdog_history(limit: int = 20) -> list[dict[str, Any]]:
        try:
            from .watchdog import watchdog

            return list(watchdog.recent(limit=limit))
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _engine_state() -> dict[str, Any]:
        try:
            from .accelerators import state as engine_state

            return engine_state(probe=True)
        except Exception as exc:  # noqa: BLE001
            return {"status": "unknown", "error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _media_state() -> dict[str, Any]:
        try:
            from .avatar import get_avatar_service
            from .speech import speech_engine
            from tools.voice import voice_available_models

            return {
                "speech": speech_engine.status(),
                "avatar": get_avatar_service().status(probe=True),
                "transcription": voice_available_models(),
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _resources() -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            import psutil  # type: ignore

            disk = psutil.disk_usage(str(Path.cwd()))
            out["disk_free_gb"] = round(disk.free / (1024 ** 3), 2)
            out["disk_percent"] = disk.percent
            mem = psutil.virtual_memory()
            out["ram_free_gb"] = round(mem.available / (1024 ** 3), 2)
            out["ram_percent"] = mem.percent
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    # ------------------------------------------------------------- stuck work
    def find_stuck_work(self, *, stuck_minutes: Optional[int] = None) -> dict[str, Any]:
        """Runs/jobs that never reached a terminal state.

        A dashboard showing "processing" forever is not a UI bug to wait out —
        it is work whose owner died.  This finds it; :meth:`reap_stuck` closes
        it.
        """
        limit = float(stuck_minutes if stuck_minutes is not None else config.doctor_stuck_minutes)
        threshold = time.time() - (limit * 60.0)
        jobs: list[dict[str, Any]] = []
        runs: list[dict[str, Any]] = []
        try:
            from gateway.queue import job_queue

            for row in job_queue.list_jobs(limit=200):
                status = str(row.get("status") or "")
                if status not in IN_FLIGHT_JOB_STATUSES:
                    continue
                created = float(row.get("created") or 0.0)
                if created and created < threshold:
                    jobs.append(
                        {
                            "kind": "job",
                            "id": row.get("id"),
                            "type": row.get("kind"),
                            "status": status,
                            "age_minutes": round((time.time() - created) / 60.0, 1),
                        }
                    )
        except Exception:  # noqa: BLE001 - queue may not be started (CLI use)
            pass
        try:
            from .run_events import run_bus

            for row in run_bus.runs():
                status = str(row.get("status") or "")
                if status not in IN_FLIGHT_RUN_STATUSES:
                    continue
                started = float(row.get("started") or 0.0)
                if started and started < threshold:
                    runs.append(
                        {
                            "kind": "run",
                            "id": row.get("run_id"),
                            "label": row.get("label"),
                            "status": status,
                            "age_minutes": round((time.time() - started) / 60.0, 1),
                        }
                    )
        except Exception:  # noqa: BLE001
            pass
        return {
            "threshold_minutes": limit,
            "jobs": jobs,
            "runs": runs,
            "count": len(jobs) + len(runs),
        }

    def reap_stuck(self, *, dry_run: bool = True, stuck_minutes: Optional[int] = None) -> dict[str, Any]:
        """Close out stuck work so no state stays open forever."""
        stuck = self.find_stuck_work(stuck_minutes=stuck_minutes)
        reaped: list[dict[str, Any]] = []
        if dry_run:
            return {"dry_run": True, "candidates": stuck, "reaped": []}
        try:
            from gateway.queue import job_queue

            for item in stuck["jobs"]:
                try:
                    res = job_queue.cancel(item["id"])
                    reaped.append({"kind": "job", "id": item["id"], "result": res})
                except Exception as exc:  # noqa: BLE001
                    reaped.append({"kind": "job", "id": item["id"], "error": str(exc)})
        except Exception:  # noqa: BLE001
            pass
        try:
            from .run_events import run_bus

            for item in stuck["runs"]:
                try:
                    run_bus.finish(
                        item["id"],
                        "error",
                        error=f"reaped by Hermus doctor after {item['age_minutes']} min without progress",
                    )
                    reaped.append({"kind": "run", "id": item["id"], "result": {"finished": True}})
                except Exception as exc:  # noqa: BLE001
                    reaped.append({"kind": "run", "id": item["id"], "error": str(exc)})
        except Exception:  # noqa: BLE001
            pass
        return {"dry_run": False, "candidates": stuck, "reaped": reaped}

    # ------------------------------------------------------------------ analyse
    def analyze(self, signals: Optional[dict[str, Any]] = None) -> list[Finding]:
        """Deterministic diagnosis — works with no model and no network."""
        signals = signals or self.collect()
        findings: list[Finding] = []

        findings.extend(self._analyze_issues(signals.get("issues") or []))
        findings.extend(self._analyze_diagnostics(signals.get("diagnostics") or {}))
        findings.extend(self._analyze_engines(signals.get("engines") or {}))
        findings.extend(self._analyze_media(signals.get("media") or {}))
        findings.extend(self._analyze_stuck(signals.get("stuck") or {}))
        findings.extend(self._analyze_resources(signals.get("resources") or {}))
        findings.extend(self._analyze_watchdog(signals.get("watchdog") or []))

        findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.category))
        return findings

    # -- grouped runtime issues --------------------------------------------
    @staticmethod
    def _analyze_issues(issues: list[dict[str, Any]]) -> list[Finding]:
        """Group structured runtime issues and turn repeats into findings."""
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for issue in issues:
            component = str(issue.get("component") or "unknown")
            error = str(issue.get("error") or "")[:160]
            key = (component, _normalize_error(error))
            bucket = groups.setdefault(
                key,
                {"component": component, "error": error, "count": 0, "operations": set(), "last": ""},
            )
            bucket["count"] += 1
            bucket["operations"].add(str(issue.get("operation") or ""))
            bucket["last"] = str(issue.get("ts") or bucket["last"])

        out: list[Finding] = []
        for (component, _norm), bucket in groups.items():
            count = bucket["count"]
            error = bucket["error"]
            severity = SEVERITY_HIGH if count >= 5 else (SEVERITY_MEDIUM if count >= 2 else SEVERITY_LOW)
            ops = ", ".join(sorted(o for o in bucket["operations"] if o)[:4]) or "n/a"
            known = KNOWN_ERROR_FIXES.match(error)
            finding = _finding(
                severity,
                "runtime_error",
                f"{component}: {error[:90]}",
                f"{count} occurrence(s) in component '{component}' (operations: {ops}); last at {bucket['last']}",
                known.fixes if known else [],
                component=component,
            )
            if known and known.references:
                finding.references = list(known.references)
            if not known:
                finding.fixes = [
                    f"Reproduce with the operation above and capture the traceback from /runtime/issues",
                    "Ask the doctor to research this signature (POST /doctor/run with ask_internet=true)",
                ]
            out.append(finding)
        return out

    @staticmethod
    def _analyze_diagnostics(diag: dict[str, Any]) -> list[Finding]:
        out: list[Finding] = []
        for check in diag.get("checks") or []:
            if check.get("ok"):
                continue
            required = check.get("level") != "recommended"
            out.append(
                _finding(
                    SEVERITY_HIGH if required else SEVERITY_LOW,
                    "install",
                    f"{check.get('name')}: {check.get('detail')}",
                    f"diagnostic '{check.get('name')}' failed ({check.get('detail')})",
                    [check.get("hint")] if check.get("hint") else [],
                    component="install",
                )
            )
        return out

    @staticmethod
    def _analyze_engines(engines: dict[str, Any]) -> list[Finding]:
        """Local engine health: installed / running / model present / reachable."""
        out: list[Finding] = []
        status = engines.get("status")
        plan = engines.get("plan") or {}
        mode = plan.get("mode")
        if status == "not_applicable":
            return out

        nollama = engines.get("nollama") or {}
        engine_states = engines.get("engines") or {}

        if status == "needs_install":
            out.append(
                _finding(
                    SEVERITY_MEDIUM,
                    "engine_missing",
                    "NoLlama is not installed but the hardware plan needs it",
                    f"plan mode '{mode}' routes at least one role to NoLlama; server not found at {nollama.get('home')}",
                    [
                        "Dashboard → System Overview → Local AI Engine → Install engine",
                        "or: POST /engine/nollama/install",
                    ],
                    component="local_engine",
                )
            )
        elif status == "needs_model":
            recommended = engines.get("recommended_model") or {}
            out.append(
                _finding(
                    SEVERITY_MEDIUM,
                    "engine_model_missing",
                    f"Local engine has no model on disk ({recommended.get('name', 'model')})",
                    "NoLlama is installed and reachable, but no OpenVINO IR directory is complete under "
                    f"{nollama.get('models_dir')}",
                    [
                        f"Dashboard → System Overview → Download {recommended.get('name') or 'the recommended model'}",
                        f"or: POST /engine/models/download {{\"model\": \"{recommended.get('id') or 'minicpm'}\"}}",
                    ],
                    component="local_engine",
                )
            )
        elif status == "unavailable" and any(
            role.get("engine") == "nollama" for role in (plan.get("roles") or {}).values()
        ):
            # Only blame NoLlama when the plan actually routes work to it — on a
            # CPU-only box "unavailable" means Ollama is down, and saying
            # otherwise sends the user to the wrong fix.
            detail = (engine_states.get("nollama") or {}).get("detail") or "no response"
            out.append(
                _finding(
                    SEVERITY_HIGH,
                    "engine_down",
                    "Local engine is not answering",
                    f"NoLlama should be serving on {engines.get('nollama_base_url')} but {detail}",
                    [
                        "Dashboard → Local AI Engine → Start engine",
                        f"check the log: {nollama.get('log') or 'nollama.log'}",
                    ],
                    component="local_engine",
                )
            )

        ollama = engine_states.get("ollama") or {}
        roles = plan.get("roles") or {}
        needs_ollama = any(r.get("engine") == "ollama" for r in roles.values())
        if needs_ollama and ollama.get("reachable") is False:
            out.append(
                _finding(
                    SEVERITY_HIGH,
                    "engine_down",
                    "Ollama is not reachable but the plan routes work to it",
                    f"GET {engines.get('ollama_base_url')}/models failed: {ollama.get('detail')}",
                    [
                        "Start Ollama: ollama serve",
                        f"Pull a model: ollama pull {config.ollama_default_model}",
                    ],
                    component="ollama",
                )
            )
        if mode == "pipelined":
            out.append(
                _finding(
                    SEVERITY_INFO,
                    "engine_plan",
                    "NPU + GPU pipelining is active",
                    "Background roles (voice, embeddings, self-repair triage) run on the NPU; "
                    "heavy generative reasoning runs on the GPU.",
                    [],
                    component="local_engine",
                )
            )
        return out

    @staticmethod
    def _analyze_media(media: dict[str, Any]) -> list[Finding]:
        out: list[Finding] = []
        if media.get("error"):
            out.append(
                _finding(
                    SEVERITY_LOW,
                    "media_status",
                    "Optional media capability probe failed",
                    str(media.get("error"))[:200],
                    ["Import core.speech, core.avatar, and tools.voice locally to reproduce the failing probe"],
                    component="media",
                )
            )
            return out

        speech = media.get("speech") or {}
        if speech and not speech.get("available"):
            out.append(
                _finding(
                    SEVERITY_LOW,
                    "speech_optional",
                    "Local speech synthesis is unavailable",
                    str((speech.get("detail") or {}).get("setup") or speech.get("detail") or "speech backend unavailable")[:220],
                    [
                        "Install piper or espeak-ng for basic offline TTS",
                        "For advanced cloning/design, install optional OmniVoice dependencies (omnivoice, torch, soundfile)",
                    ],
                    component="speech",
                )
            )

        avatar = media.get("avatar") or {}
        if avatar.get("configured") and avatar.get("available") is False:
            services = avatar.get("services") or {}
            blocked = [
                f"{name}: {(row or {}).get('detail') or 'unreachable'}"
                for name, row in services.items()
                if (row or {}).get("reachable") is not True
            ]
            out.append(
                _finding(
                    SEVERITY_LOW,
                    "avatar_optional",
                    "Talking-avatar connector is configured but not reachable",
                    "; ".join(blocked)[:220] or "local HeyGem-style services did not answer",
                    [
                        "Start the local HeyGem-compatible services on ports 18180 and 8383/easy, or disable the connector URLs",
                        "Use /speech/avatar/status?probe=true to verify both localhost services respond",
                    ],
                    component="avatar",
                )
            )

        transcription = media.get("transcription") or {}
        local_engine = transcription.get("local_engine") or {}
        if not local_engine.get("ready") and int(transcription.get("discovered_count") or 0) == 0:
            out.append(
                _finding(
                    SEVERITY_LOW,
                    "transcription_optional",
                    "No local STT accelerator or discovered model assets",
                    "voice commands will fall back to slower/default transcription paths until a model asset is installed",
                    [
                        "Place Whisper GGML/GGUF or Parakeet assets in a configured handy-model-dir and re-check /speech/transcription/models",
                        "Or download a Whisper/OpenVINO model through the Local AI Engine panel",
                    ],
                    component="transcription",
                )
            )
        return out

    @staticmethod
    def _analyze_stuck(stuck: dict[str, Any]) -> list[Finding]:
        count = int(stuck.get("count") or 0)
        if not count:
            return []
        jobs = stuck.get("jobs") or []
        runs = stuck.get("runs") or []
        examples = ", ".join(
            [f"job {j.get('id')} ({j.get('age_minutes')} min)" for j in jobs[:3]]
            + [f"run {r.get('id')} ({r.get('age_minutes')} min)" for r in runs[:3]]
        )
        return [
            _finding(
                SEVERITY_HIGH,
                "stuck_work",
                f"{count} run(s)/job(s) stuck in a non-terminal state",
                f"Older than {stuck.get('threshold_minutes')} minutes: {examples}",
                [
                    "POST /doctor/reap {\"dry_run\": false} to close them out with an explicit reason",
                    "Check the owning worker: a crash mid-run leaves the job 'running' forever",
                ],
                component="queue",
                auto_fixable=True,
            )
        ]

    @staticmethod
    def _analyze_resources(resources: dict[str, Any]) -> list[Finding]:
        out: list[Finding] = []
        free = resources.get("disk_free_gb")
        if isinstance(free, (int, float)) and free < 5:
            out.append(
                _finding(
                    SEVERITY_HIGH,
                    "disk",
                    f"Only {free} GB of disk left",
                    f"disk usage {resources.get('disk_percent')}%",
                    [
                        "Clear data/doctor reports and data/jobs logs",
                        "Model downloads need ~1-9 GB free; free space before downloading",
                    ],
                    component="host",
                )
            )
        ram = resources.get("ram_free_gb")
        if isinstance(ram, (int, float)) and ram < 2:
            out.append(
                _finding(
                    SEVERITY_MEDIUM,
                    "memory",
                    f"Only {ram} GB of RAM available",
                    f"memory usage {resources.get('ram_percent')}%",
                    [
                        "Use the 1-3B INT4 models; 8B builds will fall back to CPU or fail to load",
                    ],
                    component="host",
                )
            )
        return out

    @staticmethod
    def _analyze_watchdog(history: list[dict[str, Any]]) -> list[Finding]:
        failed = [h for h in history if h.get("ok") is False]
        if not failed:
            return []
        last = failed[-1]
        return [
            _finding(
                SEVERITY_MEDIUM,
                "watchdog",
                f"Watchdog could not repair {len(failed)} error(s)",
                f"latest: {str(last.get('error'))[:160]} (category {last.get('category')})",
                ["Run the doctor with the model enabled: POST /doctor/run {\"use_llm\": true}"],
                component="watchdog",
            )
        ]

    # ---------------------------------------------------------------- research
    def research(self, findings: list[Finding], *, max_queries: int = 3, max_results: int = 3) -> dict[str, Any]:
        """Look up what the doctor does not already know how to fix.

        Only findings without a built-in fix are searched, and only real search
        results are returned — if ``duckduckgo_search`` is missing the module
        falls back to a mock result, which would be worse than no research at
        all, so it is reported as ``offline`` instead.
        """
        targets = [f for f in findings if not f.fixes and f.severity in (SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM)]
        if not targets:
            return {"performed": False, "reason": "no unexplained findings", "queries": []}
        try:
            from tools.web_search import DDG_AVAILABLE, web_search
        except Exception as exc:  # noqa: BLE001
            return {"performed": False, "reason": f"web search unavailable: {exc}", "queries": []}
        if not DDG_AVAILABLE:
            return {
                "performed": False,
                "offline": True,
                "reason": "duckduckgo-search is not installed (pip install duckduckgo-search)",
                "queries": [],
            }
        queries: list[dict[str, Any]] = []
        for finding in targets[: max(1, int(max_queries))]:
            query = f"hermus agent {finding.category} {finding.title}"[:180]
            try:
                results = web_search(query, max_results=max_results)
            except Exception as exc:  # noqa: BLE001
                queries.append({"query": query, "error": str(exc)})
                continue
            clean = [
                {"title": r.get("title"), "url": r.get("href"), "snippet": (r.get("body") or "")[:240]}
                for r in (results or [])
                if r.get("href")
            ]
            finding.references = [r["url"] for r in clean if r.get("url")][:3]
            queries.append({"query": query, "finding_id": finding.id, "results": clean})
        return {"performed": True, "queries": queries}

    # ------------------------------------------------------------------ triage
    def _ensure_local_engine(self, model_ref: str, timeout: float = 30.0) -> bool:
        """Start a downloaded NoLlama model for the doctor and wait for it.

        The doctor is supposed to be the small local model even on a CPU-only
        box. If a user downloaded MiniCPM but the engine is not running, Hermus
        should start it rather than silently routing the doctor to
        ``ollama/llama3.1:8b``.
        """
        provider = str(model_ref or "").split("/", 1)[0].lower()
        model = str(model_ref or "").split("/", 1)[1] if "/" in str(model_ref or "") else ""
        if provider != "nollama":
            return True  # nothing to ensure

        import requests
        from time import time as _now

        def models_served() -> list[str]:
            try:
                from .nollama import nollama_manager as _nm

                resp = requests.get(f"{_nm.base_url.rstrip('/')}/models", timeout=1.5)
                if resp.status_code != 200:
                    return []
                data = resp.json() if resp.content else {}
                return [str(m.get("id") or "") for m in (data.get("data") or []) if isinstance(m, dict)]
            except Exception:
                return []

        def model_present() -> bool:
            if not model:
                return True
            served = models_served()
            return any(name == model or name == model.split(":")[0] for name in served)

        try:
            from .nollama import nollama_manager

            if nollama_manager.running():
                if model_present():
                    return True
                # Engine is up but it was started without the downloaded model
                # (e.g. an older start without ``--model-dir``). Restart it with
                # the catalog model before letting the doctor fail on it.
                stopped = nollama_manager.stop()
                if not stopped.get("stopped"):
                    return False
            if not nollama_manager.installed() or not nollama_manager.venv_ready():
                return False
            row = (
                nollama_manager.best_installed_model("CPU", ("doctor",))
                or nollama_manager.best_installed_model("GPU", ("doctor",))
            )
            if not row:
                return False
            result = nollama_manager.start(device="AUTO")
            if not result.get("success"):
                return False

            deadline = _now() + timeout
            while _now() < deadline:
                if model_present():
                    return True
                import time as _time

                _time.sleep(1.0)
            return False
        except Exception:
            return False

    def _prefer_downloaded_doctor(self, ref: str) -> str:
        """Return a NoLlama doctor ref when MiniCPM/openvino IR is on disk.

        Even on a CPU-only plan (which used to resolve to ``ollama/llama3.1:8b``
        by default) the Hermus doctor should prefer the small model the user
        downloaded for it.
        """
        provider = str(ref or "").split("/", 1)[0].lower()
        if provider in ("nollama", "mock"):
            return ref
        try:
            from .nollama import nollama_manager

            row = (
                nollama_manager.best_installed_model("CPU", ("doctor",))
                or nollama_manager.best_installed_model("GPU", ("doctor",))
            )
            if row and row.get("repo"):
                return f"nollama/{str(row['repo']).split('/')[-1]}"
        except Exception:
            pass
        return ref

    def _configured_doctor_fallback(self) -> Optional[tuple[str, str]]:
        """A configured API provider to use if the local engine is unavailable."""
        try:
            from .provider_resolver import select_usable_bundle

            bundle = select_usable_bundle(require_tools=False)
            if not bundle:
                return None
            provider = (bundle.get("provider") or "").lower()
            model = bundle.get("default_model") or ""
            if provider and model:
                return f"{provider}/{model}", provider
        except Exception:
            pass
        return None

    def _doctor_llm(self, model: Optional[str] = None):
        """Build the LLM the doctor speaks through (its own engine role)."""
        from .accelerators import model_ref_for
        from .llm import FreeLLM

        ref = model or getattr(config, "doctor_model", "") or model_ref_for("doctor")
        if not ref:
            return None, ""
        ref = self._prefer_downloaded_doctor(ref)
        if str(ref or "").split("/", 1)[0].lower() == "nollama":
            if self._ensure_local_engine(ref):
                return FreeLLM(model=ref), ref
            # The download exists but NoLlama isn't installed/running (or was
            # explicitly disabled). Never silently drop to ollama/llama3.1:8b;
            # use any configured API provider instead, so the doctor keeps
            # working with "anything" configured.
            fb = self._configured_doctor_fallback()
            if fb:
                return FreeLLM(model=fb[0]), fb[0]
        return FreeLLM(model=ref), ref

    def triage(
        self,
        findings: list[Finding],
        signals: Optional[dict[str, Any]] = None,
        *,
        model: Optional[str] = None,
        max_findings: int = 8,
    ) -> dict[str, Any]:
        """Ask the small doctor model for a plain-language explanation + plan.

        Falls back to a deterministic summary when no model is reachable, so the
        report is always complete.
        """
        top = findings[: max(1, int(max_findings))]
        if not top:
            return {
                "model": "",
                "used_model": False,
                "summary": "No findings — Hermus looks healthy on every signal checked.",
                "management_plan": [],
            }
        deterministic = _deterministic_summary(top)
        llm, ref = self._doctor_llm(model)
        if llm is None:
            deterministic["used_model"] = False
            deterministic["model"] = ""
            deterministic["note"] = "No doctor model configured; deterministic triage only."
            return deterministic
        prompt = _triage_prompt(top, signals)
        try:
            response = llm.chat([{"role": "user", "content": prompt}])
            text = (response.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            deterministic["used_model"] = False
            deterministic["model"] = ref
            deterministic["note"] = f"Doctor model unreachable ({type(exc).__name__}); deterministic triage only."
            return deterministic
        if not text or text.lower().startswith("nollama error") or "no api key" in text.lower():
            deterministic["used_model"] = False
            deterministic["model"] = ref
            deterministic["note"] = f"Doctor model '{ref}' did not answer; deterministic triage only."
            return deterministic
        return {
            "model": ref,
            "used_model": True,
            "summary": text[:4000],
            "management_plan": deterministic["management_plan"],
        }

    # -------------------------------------------------------------------- run
    def run(
        self,
        *,
        ask_internet: Optional[bool] = None,
        use_llm: bool = True,
        reap: bool = False,
        model: Optional[str] = None,
        stuck_minutes: Optional[int] = None,
        max_findings: int = 8,
        save: bool = True,
        auto: bool = False,
    ) -> dict[str, Any]:
        """Full examination: signals → findings → research → triage → report."""
        if auto and not self._auto_allowed():
            return {
                "status": "skipped",
                "reason": "auto-triage cooldown or daily cap reached",
                "cooldown_minutes": config.doctor_cooldown_minutes,
                "daily_cap": config.doctor_daily_cap,
            }
        if ask_internet is None:
            ask_internet = bool(getattr(config, "doctor_ask_internet", True))

        signals = self.collect(stuck_minutes=stuck_minutes)
        findings = self.analyze(signals)
        research: dict[str, Any] = {"performed": False, "reason": "disabled", "queries": []}
        if ask_internet:
            research = self.research(findings)
        triage = (
            self.triage(findings, signals, model=model, max_findings=max_findings)
            if use_llm
            else _deterministic_summary(findings[:max_findings]) | {"used_model": False, "model": ""}
        )
        reap_result = self.reap_stuck(dry_run=not reap, stuck_minutes=stuck_minutes)

        worst = findings[0].severity if findings else SEVERITY_INFO
        # "critical" is reserved for critical findings — calling a high finding
        # critical trains the user to ignore the label.
        status = _overall_status(worst, findings)
        report = {
            "id": f"dr-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            "ts": _now(),
            "status": status,
            "worst_severity": worst,
            "counts": _count_severities(findings),
            "engine": {
                "mode": (signals.get("engines") or {}).get("plan", {}).get("mode"),
                "status": (signals.get("engines") or {}).get("status"),
                "doctor_model": triage.get("model") or "",
            },
            "findings": [f.to_dict() for f in findings],
            "stuck": signals.get("stuck") or {},
            "reap": reap_result,
            "research": research,
            "triage": triage,
            "signals_summary": {
                "issues": len(signals.get("issues") or []),
                "diagnostics_failed": sum(
                    1 for c in (signals.get("diagnostics") or {}).get("checks", []) if not c.get("ok")
                ),
                "watchdog_failures": len(
                    [h for h in (signals.get("watchdog") or []) if h.get("ok") is False]
                ),
                "media": signals.get("media") or {},
                "resources": signals.get("resources") or {},
            },
        }
        if save:
            report["path"] = self.save_report(report)
        self._runs.append(report)
        self._runs = self._runs[-20:]
        if auto:
            self._auto_last = time.time()
            self._auto_count += 1
        _record_lessons(report)
        return report

    def _auto_allowed(self) -> bool:
        if not getattr(config, "doctor_auto", False):
            return False
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._auto_today != today:
            self._auto_today = today
            self._auto_count = 0
        if self._auto_count >= int(getattr(config, "doctor_daily_cap", 12)):
            return False
        cooldown = int(getattr(config, "doctor_cooldown_minutes", 15)) * 60.0
        return (time.time() - self._auto_last) >= cooldown

    # ---------------------------------------------------------------- reports
    def save_report(self, report: dict[str, Any]) -> str:
        """Persist JSON + Markdown so the user has something to read later."""
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            json_path = self.reports_dir / f"{report['id']}.json"
            json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            md_path = self.reports_dir / f"{report['id']}.md"
            md_path.write_text(to_markdown(report), encoding="utf-8")
            return str(md_path)
        except OSError:
            return ""

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Most recent reports, newest first (memory first, then disk)."""
        out = list(reversed(self._runs))[: max(1, int(limit))]
        if out:
            return out
        try:
            files = sorted(self.reports_dir.glob("dr-*.json"), reverse=True)[: max(1, int(limit))]
        except OSError:
            return []
        for path in files:
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                continue
        return out

    def status(self) -> dict[str, Any]:
        """Cheap status for the dashboard (no LLM, no network)."""
        signals = self.collect()
        findings = self.analyze(signals)
        llm, ref = self._doctor_llm()
        engine = signals.get("engines") or {}
        media = signals.get("media") or {}
        return {
            "enabled": bool(getattr(config, "doctor_enabled", True)),
            "auto": bool(getattr(config, "doctor_auto", False)),
            "ask_internet": bool(getattr(config, "doctor_ask_internet", True)),
            "model": ref,
            "model_available": bool(ref) and (engine.get("status") in ("ready", "not_applicable")),
            "engine_status": engine.get("status"),
            "engine_mode": (engine.get("plan") or {}).get("mode"),
            "media": media,
            "worst_severity": findings[0].severity if findings else SEVERITY_INFO,
            "counts": _count_severities(findings),
            "finding_count": len(findings),
            "stuck": signals.get("stuck") or {},
            "reports": [
                {"id": r.get("id"), "ts": r.get("ts"), "status": r.get("status"), "path": r.get("path")}
                for r in self.recent(5)
            ],
        }


# ---------------------------------------------------------------------------
# Known signatures: fixes Hermus can state without asking a model.
# ---------------------------------------------------------------------------
@dataclass
class _KnownFix:
    pattern: str
    fixes: list[str]
    references: list[str] = field(default_factory=list)

    def match(self, error: str) -> bool:
        return re.search(self.pattern, error, re.I) is not None


class _KnownFixTable:
    def __init__(self, entries: list[_KnownFix]) -> None:
        self._entries = entries

    def match(self, error: str) -> Optional[_KnownFix]:
        for entry in self._entries:
            if entry.match(error or ""):
                return entry
        return None


KNOWN_ERROR_FIXES = _KnownFixTable(
    [
        _KnownFix(
            r"database is locked",
            [
                "A writer is holding the WAL lock: raise PRAGMA busy_timeout or shorten the write transaction",
                "Check for a second gateway process pointing at the same data/ directory",
            ],
        ),
        _KnownFix(
            r"unclosed database|ResourceWarning",
            [
                "Connections must go through core.db_registry.open_db/using so shutdown closes them",
                "core.db_registry.close_all() runs in the gateway lifespan finally: block",
            ],
        ),
        _KnownFix(
            r"modulenotfounderror|no module named",
            ["pip install -r requirements.txt (or install the missing module named in the error)"],
        ),
        _KnownFix(
            r"connection refused|connectionerror|max retries",
            [
                "The local engine is not listening: start Ollama (`ollama serve`) or NoLlama (dashboard → Local AI Engine)",
                "Verify the port in HERMUS_NOLLAMA_PORT / HERMUS_OLLAMA_BASE_URL",
            ],
        ),
        _KnownFix(
            r"no_evidence_of_work",
            [
                "A stage described work instead of doing it: the evidence gate rejected it",
                "Re-run with a coder-role stage, or raise HERMUS_MISSION_BUDGET_STEPS",
            ],
        ),
        _KnownFix(
            r"rate.?limit|429",
            ["Add a second key for the provider (dashboard → API Keys) so the pool can fail over"],
        ),
        _KnownFix(
            r"timed out|timeout",
            ["Increase the job timeout, or route long work to a mission instead of a single turn"],
        ),
        _KnownFix(
            r"compilation failed|vpux|npu",
            [
                "NPU builds must be channel-wise INT4 (-int4-cw-ov) and ≤ 8B parameters",
                "On Linux the intel-npu-driver and intel-npu-compiler versions must match each other and OpenVINO",
            ],
            references=["https://github.com/intel/linux-npu-driver/releases"],
        ),
    ]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_error(error: str) -> str:
    """Collapse the parts of an error that change every time (ids, addresses)."""
    text = re.sub(r"0x[0-9a-f]+", "0x…", error or "")
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{20,}\b", "<id>", text)
    text = re.sub(r"\b\d{3,}\b", "N", text)
    return text.strip()[:160]


def _count_severities(findings: list[Finding]) -> dict[str, int]:
    counts = {key: 0 for key in _SEVERITY_ORDER}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts


def _overall_status(worst: str, findings: list[Finding]) -> str:
    """Map the worst finding to an overall verdict.

    ``critical`` is reserved for critical findings, ``attention`` covers
    high/medium, and informational/low findings alone still read as ``ok`` —
    a label that cries wolf gets ignored.
    """
    if not findings:
        return "ok"
    if worst == SEVERITY_CRITICAL:
        return "critical"
    if worst in (SEVERITY_HIGH, SEVERITY_MEDIUM):
        return "attention"
    return "ok"


def _deterministic_summary(findings: list[Finding]) -> dict[str, Any]:
    """A useful report even with no model available."""
    if not findings:
        return {
            "summary": "No findings — every signal the doctor checks is clean.",
            "management_plan": [],
        }
    worst = findings[0]
    lines = [
        f"{len(findings)} finding(s); worst is {worst.severity}: {worst.title}."
    ]
    plan: list[str] = []
    for finding in findings[:6]:
        if finding.fixes:
            plan.append(f"{finding.title} → {finding.fixes[0]}")
        else:
            plan.append(f"{finding.title} → needs investigation (no built-in fix)")
    return {"summary": " ".join(lines), "management_plan": plan}


def _triage_prompt(findings: list[Finding], signals: Optional[dict[str, Any]]) -> str:
    """Compact prompt sized for a 1-3B model: short, structured, no preamble."""
    lines = ["You are the internal doctor for the Hermus agent runtime. Be brief and concrete.", ""]
    lines.append("FINDINGS:")
    for finding in findings[:8]:
        lines.append(f"- [{finding.severity}] {finding.title} | {finding.evidence[:160]}")
        if finding.fixes:
            lines.append(f"  known fix: {finding.fixes[0][:160]}")
    engines = (signals or {}).get("engines") or {}
    lines.append("")
    lines.append(
        "CONTEXT: engine mode="
        f"{(engines.get('plan') or {}).get('mode')} status={engines.get('status')} "
        f"issues={len((signals or {}).get('issues') or [])} "
        f"stuck={((signals or {}).get('stuck') or {}).get('count', 0)}"
    )
    lines.append("")
    lines.append("Answer in this exact shape, under 160 words:")
    lines.append("WHAT WENT WRONG: <one or two sentences>")
    lines.append("WHY: <root cause>")
    lines.append("HOW TO MANAGE IT: <3-5 short imperative steps>")
    return "\n".join(lines)


def to_markdown(report: dict[str, Any]) -> str:
    """Human-readable report — this is what the user is asked to read."""
    lines = [
        f"# Hermus Doctor Report — {report.get('ts', '')}",
        "",
        f"**Status:** {report.get('status', 'unknown').upper()}  ",
        f"**Worst severity:** {report.get('worst_severity', 'n/a')}  ",
        f"**Engine:** mode `{(report.get('engine') or {}).get('mode')}`, "
        f"status `{(report.get('engine') or {}).get('status')}`, "
        f"doctor model `{(report.get('engine') or {}).get('doctor_model') or 'none'}`",
        "",
        "## What went wrong and how to manage it",
        "",
        str((report.get("triage") or {}).get("summary") or "_No summary._"),
        "",
    ]
    plan = (report.get("triage") or {}).get("management_plan") or []
    if plan:
        lines += ["### Management plan", ""]
        lines += [f"{i}. {step}" for i, step in enumerate(plan, start=1)]
        lines.append("")
    counts = report.get("counts") or {}
    if counts:
        lines += ["## Findings by severity", ""]
        lines += [f"- **{sev}**: {counts.get(sev, 0)}" for sev in ("critical", "high", "medium", "low", "info")]
        lines.append("")
    findings = report.get("findings") or []
    if findings:
        lines += ["## Findings", ""]
        for finding in findings:
            lines.append(f"### [{finding.get('severity', '').upper()}] {finding.get('title', '')}")
            lines.append(f"- **Category:** {finding.get('category', '')} ({finding.get('component', 'n/a')})")
            lines.append(f"- **Evidence:** {finding.get('evidence', '')}")
            if finding.get("fixes"):
                lines.append("- **Fixes:**")
                lines += [f"  - {fix}" for fix in finding["fixes"]]
            if finding.get("references"):
                lines.append("- **References:**")
                lines += [f"  - {ref}" for ref in finding["references"]]
            lines.append("")
    stuck = report.get("stuck") or {}
    if stuck.get("count"):
        lines += [
            "## Stuck work (nothing may stay in a processing state)",
            "",
            f"- jobs: {len(stuck.get('jobs') or [])}, runs: {len(stuck.get('runs') or [])}, "
            f"threshold {stuck.get('threshold_minutes')} minutes",
            "",
        ]
    research = report.get("research") or {}
    if research.get("performed"):
        lines += ["## Internet research", ""]
        for entry in research.get("queries") or []:
            lines.append(f"- **Query:** {entry.get('query')}")
            for result in entry.get("results") or []:
                lines.append(f"  - [{result.get('title')}]({result.get('url')}) — {(result.get('snippet') or '')[:160]}")
        lines.append("")
    elif research.get("reason"):
        lines += ["## Internet research", "", f"_Not performed: {research.get('reason')}_", ""]
    return "\n".join(lines)


def _record_lessons(report: dict[str, Any]) -> None:
    """Feed the top finding back into the lessons loop so the agent learns it."""
    try:
        from .reasoning.lessons import lessons_store

        for finding in (report.get("findings") or [])[:3]:
            fixes = finding.get("fixes") or []
            if not fixes:
                continue
            lessons_store.add(
                f"[{finding.get('severity')}] {finding.get('title')}: {fixes[0]}",
                category=f"doctor:{finding.get('category')}",
                source="hermus-doctor",
            )
    except Exception:  # noqa: BLE001 - lessons are a bonus, never a failure
        pass


doctor = HermusDoctor()
