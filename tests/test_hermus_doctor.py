"""Hermus Doctor — Hermus's own physician.

The doctor's customer is Hermus, not the user, so these tests pin the things
that make it trustworthy:

* diagnosis is **deterministic** (no model, no network needed),
* stuck runs/jobs are found and can be reaped (nothing stays in "processing"),
* the small-model triage degrades to a useful report when the model is down,
* "ask the internet" never presents the offline mock search as research,
* reports are written to disk in both JSON and Markdown.
"""
from __future__ import annotations

import json
import time

import pytest

from core.doctor import (
    SEVERITY_HIGH,
    SEVERITY_INFO,
    Finding,
    HermusDoctor,
    KNOWN_ERROR_FIXES,
    to_markdown,
)


@pytest.fixture
def doc(tmp_path):
    return HermusDoctor(reports_dir=str(tmp_path / "doctor"))


def signals(**overrides):
    base = {
        "collected_at": "2026-08-30T10:00:00+00:00",
        "issues": [],
        "stuck": {"threshold_minutes": 20, "jobs": [], "runs": [], "count": 0},
        "diagnostics": {"checks": []},
        "watchdog": [],
        "engines": {"status": "ready", "plan": {"mode": "cpu_only", "roles": {}},
                    "engines": {"ollama": {"reachable": True, "detail": "200"},
                                "nollama": {"reachable": True, "detail": "200"}},
                    "nollama": {"installed": True, "models_dir": "/tmp/models",
                                "home": "/tmp/nollama", "log": "data/nollama.log"},
                    "recommended_model": {"id": "minicpm", "name": "MiniCPM5 1B"},
                    "nollama_base_url": "http://localhost:8010/v1",
                    "ollama_base_url": "http://localhost:11434/v1"},
        "media": {
            "speech": {"available": True, "detail": {}},
            "avatar": {
                "configured": False,
                "available": False,
                "services": {
                    "tts": {"reachable": None, "detail": None},
                    "face2face": {"reachable": None, "detail": None},
                },
            },
            "transcription": {"local_engine": {"ready": True}, "discovered_count": 1},
        },
        "resources": {"disk_free_gb": 40.0, "disk_percent": 40, "ram_free_gb": 8.0, "ram_percent": 40},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Deterministic analysis
# ---------------------------------------------------------------------------
def test_clean_signals_produce_no_findings(doc):
    assert doc.analyze(signals()) == []


def test_repeated_runtime_issue_becomes_a_finding(doc):
    issues = [
        {"component": "memory", "operation": "remember", "error": "database is locked", "ts": "t1"},
        {"component": "memory", "operation": "recall", "error": "database is locked", "ts": "t2"},
    ]
    findings = doc.analyze(signals(issues=issues))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "runtime_error"
    assert finding.severity == "medium", "2 occurrences = medium, 5+ = high"
    assert "database is locked" in finding.title
    # Known signature → a concrete fix, and grouping collapsed the two rows.
    assert any("busy_timeout" in fix for fix in finding.fixes)
    assert "2 occurrence(s)" in finding.evidence


def test_issue_grouping_ignores_volatile_ids(doc):
    """0x addresses / uuids must not split one bug into a hundred findings."""
    issues = [
        {"component": "agent", "error": "unclosed database in <sqlite3.Connection object at 0x7f1a>", "ts": "t"},
        {"component": "agent", "error": "unclosed database in <sqlite3.Connection object at 0x9c2b>", "ts": "t"},
    ]
    findings = doc.analyze(signals(issues=issues))
    assert len(findings) == 1
    assert findings[0].evidence.startswith("2 occurrence(s)")


def test_failed_diagnostics_are_reported_with_their_hint(doc):
    diag = {"checks": [
        {"name": "pydantic", "ok": True, "level": "required"},
        {"name": "fastapi", "ok": False, "level": "recommended", "detail": "installed=False",
         "hint": "pip install fastapi"},
        {"name": "data_dir", "ok": False, "level": "required", "detail": "read-only",
         "hint": "make data/ writable"},
    ]}
    findings = doc.analyze(signals(diagnostics=diag))
    severities = {f.category: f.severity for f in findings}
    assert severities == {"install": SEVERITY_HIGH} or len(findings) == 2
    assert any(f.severity == SEVERITY_HIGH for f in findings)
    assert any("pip install fastapi" in f.fixes for f in findings)


def test_missing_engine_is_explained_with_the_dashboard_route(doc):
    engines = signals()["engines"]
    engines["status"] = "needs_install"
    findings = doc.analyze(signals(engines=engines,
                                   issues=[]))
    cats = {f.category for f in findings}
    assert "engine_missing" in cats
    fix = next(f for f in findings if f.category == "engine_missing").fixes[0]
    assert "Local AI Engine" in fix


def test_missing_model_points_at_the_download_button(doc):
    engines = signals()["engines"]
    engines["status"] = "needs_model"
    findings = doc.analyze(signals(engines=engines))
    finding = next(f for f in findings if f.category == "engine_model_missing")
    assert "MiniCPM5 1B" in finding.title
    assert any("/engine/models/download" in fix for fix in finding.fixes)


def test_dead_engine_is_high_severity(doc):
    engines = signals()["engines"]
    engines["status"] = "unavailable"
    engines["plan"] = {"mode": "npu_only",
                       "roles": {"reasoning": {"engine": "nollama", "device": "NPU"}}}
    findings = doc.analyze(signals(engines=engines))
    finding = next(f for f in findings if f.category == "engine_down")
    assert finding.severity == SEVERITY_HIGH
    assert any("nollama.log" in fix for fix in finding.fixes)


def test_unavailable_does_not_blame_nollama_on_a_cpu_box(doc):
    """On a CPU-only plan, 'unavailable' means Ollama — not the Intel engine."""
    engines = signals()["engines"]
    engines["status"] = "unavailable"
    engines["plan"] = {"mode": "cpu_only", "roles": {"reasoning": {"engine": "ollama", "device": "CPU"}}}
    engines["engines"] = {"ollama": {"reachable": False, "detail": "ConnectionError"},
                          "nollama": {"reachable": False, "detail": "ConnectionError"}}
    findings = doc.analyze(signals(engines=engines))
    components = {f.component for f in findings if f.category == "engine_down"}
    assert components == {"ollama"}
    assert not any("NoLlama should be serving" in f.evidence for f in findings)


def test_overall_status_reserves_critical_for_critical_findings(doc, monkeypatch):
    from core.doctor import SEVERITY_CRITICAL, SEVERITY_LOW, SEVERITY_MEDIUM, _overall_status

    finding = lambda sev: Finding(id="f", severity=sev, category="c", title="t", evidence="e")
    assert _overall_status(SEVERITY_INFO, []) == "ok"
    assert _overall_status(SEVERITY_LOW, [finding(SEVERITY_LOW)]) == "ok"
    assert _overall_status(SEVERITY_MEDIUM, [finding(SEVERITY_MEDIUM)]) == "attention"
    assert _overall_status(SEVERITY_HIGH, [finding(SEVERITY_HIGH)]) == "attention"
    assert _overall_status(SEVERITY_CRITICAL, [finding(SEVERITY_CRITICAL)]) == "critical"


def test_ollama_down_while_the_plan_needs_it(doc):
    engines = signals()["engines"]
    engines["status"] = "unavailable"
    engines["plan"] = {"mode": "cpu_only", "roles": {"reasoning": {"engine": "ollama", "device": "CPU"}}}
    engines["engines"] = {"ollama": {"reachable": False, "detail": "ConnectionError"},
                          "nollama": {"reachable": False, "detail": "no"}}
    findings = doc.analyze(signals(engines=engines))
    assert any(f.component == "ollama" for f in findings)


def test_pipelined_plan_is_reported_as_info(doc):
    engines = signals()["engines"]
    engines["plan"] = {"mode": "pipelined", "roles": {}}
    findings = doc.analyze(signals(engines=engines))
    info = [f for f in findings if f.category == "engine_plan"]
    assert info and info[0].severity == SEVERITY_INFO


def test_low_disk_and_ram_are_findings(doc):
    findings = doc.analyze(signals(resources={"disk_free_gb": 2.1, "disk_percent": 96,
                                              "ram_free_gb": 0.8, "ram_percent": 95}))
    cats = {f.category for f in findings}
    assert cats == {"disk", "memory"}
    assert next(f for f in findings if f.category == "disk").severity == SEVERITY_HIGH


def test_optional_media_findings_are_honest(doc):
    media = {
        "speech": {"available": False, "detail": {"setup": "install piper or omnivoice"}},
        "avatar": {
            "configured": True,
            "available": False,
            "services": {
                "tts": {"reachable": False, "detail": "ConnectionError"},
                "face2face": {"reachable": True, "detail": "http 200"},
            },
        },
        "transcription": {"local_engine": {"ready": False}, "discovered_count": 0},
    }
    findings = doc.analyze(signals(media=media))
    cats = {f.category for f in findings}
    assert {"speech_optional", "avatar_optional", "transcription_optional"} <= cats


def test_media_probe_failure_is_reported(doc):
    findings = doc.analyze(signals(media={"error": "ImportError: boom"}))
    finding = next(f for f in findings if f.category == "media_status")
    assert finding.severity == "low"
    assert "boom" in finding.evidence


def test_findings_are_sorted_worst_first(doc):
    engines = signals()["engines"]
    engines["status"] = "unavailable"
    found = doc.analyze(signals(engines=engines, resources={"disk_free_gb": 1.0, "ram_free_gb": 0.5}))
    assert [f.severity for f in found] == sorted([f.severity for f in found],
                                                 key=lambda s: {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}[s])


def test_known_error_table_covers_the_shutdown_leak(doc):
    match = KNOWN_ERROR_FIXES.match("ResourceWarning: unclosed database in <sqlite3.Connection>")
    assert match is not None
    assert any("db_registry" in fix for fix in match.fixes)


# ---------------------------------------------------------------------------
# Stuck work — nothing may stay in a processing state
# ---------------------------------------------------------------------------
def test_stuck_work_is_found_and_reaped(doc, monkeypatch):
    class FakeQueue:
        def __init__(self):
            self.cancelled = []

        def list_jobs(self, limit=200):
            return [
                {"id": "job-1", "kind": "runtime.turn", "status": "running", "created": time.time() - 3600},
                {"id": "job-2", "kind": "agent.chat", "status": "queued", "created": time.time() - 7200},
                {"id": "job-3", "kind": "agent.chat", "status": "succeeded", "created": time.time() - 7200},
                {"id": "job-4", "kind": "agent.chat", "status": "running", "created": time.time() - 5},
            ]

        def cancel(self, job_id):
            self.cancelled.append(job_id)
            return {"cancelled": True, "job_id": job_id}

    fake = FakeQueue()
    import gateway.queue as queue_mod

    monkeypatch.setattr(queue_mod.job_queue, "list_jobs", fake.list_jobs)
    monkeypatch.setattr(queue_mod.job_queue, "cancel", fake.cancel)

    from core.run_events import run_bus

    run_bus.start("stuck-run", label="long mission")
    run_bus.get("stuck-run").started = time.time() - 5400

    stuck = doc.find_stuck_work()
    ids = {item["id"] for item in stuck["jobs"]}
    assert ids == {"job-1", "job-2"}, "terminal and fresh jobs are not stuck"
    assert any(item["id"] == "stuck-run" for item in stuck["runs"])
    assert stuck["count"] == 3

    findings = doc.analyze(signals(stuck=stuck))
    stuck_finding = next(f for f in findings if f.category == "stuck_work")
    assert stuck_finding.severity == SEVERITY_HIGH
    assert stuck_finding.auto_fixable is True

    preview = doc.reap_stuck(dry_run=True)
    assert preview["dry_run"] is True and preview["reaped"] == []
    result = doc.reap_stuck(dry_run=False)
    assert set(fake.cancelled) == {"job-1", "job-2"}
    assert any(item["kind"] == "run" and item["id"] == "stuck-run" for item in result["reaped"])
    assert run_bus.get("stuck-run").status == "error"


def test_no_stuck_work_is_not_a_finding(doc):
    assert doc.analyze(signals()) == []
    assert doc.reap_stuck(dry_run=True)["candidates"]["count"] == 0


# ---------------------------------------------------------------------------
# Research (ask the internet)
# ---------------------------------------------------------------------------
def test_research_is_skipped_when_everything_has_a_fix(doc):
    out = doc.research([Finding(id="f1", severity=SEVERITY_HIGH, category="x", title="t",
                                evidence="e", fixes=["do this"])])
    assert out["performed"] is False
    assert "no unexplained findings" in out["reason"]


def test_research_refuses_to_pass_mock_results_off_as_research(doc, monkeypatch):
    """Without duckduckgo_search, web_search returns a mock — never show it."""
    import tools.web_search as ws

    monkeypatch.setattr(ws, "DDG_AVAILABLE", False)
    findings = [Finding(id="f1", severity=SEVERITY_HIGH, category="mystery", title="weird crash",
                        evidence="e", fixes=[])]
    out = doc.research(findings)
    assert out["performed"] is False
    assert out["offline"] is True
    assert findings[0].references == []


def test_research_attaches_real_references(doc, monkeypatch):
    import tools.web_search as ws

    monkeypatch.setattr(ws, "DDG_AVAILABLE", True)
    monkeypatch.setattr(
        ws,
        "web_search",
        lambda query, max_results=5: [
            {"title": "Fix A", "href": "https://example.com/a", "body": "do this"},
            {"title": "No link", "href": "", "body": "useless"},
        ],
    )
    findings = [Finding(id="f1", severity=SEVERITY_HIGH, category="mystery", title="weird crash",
                        evidence="e", fixes=[])]
    out = doc.research(findings)
    assert out["performed"] is True
    assert findings[0].references == ["https://example.com/a"], "linkless results are dropped"
    assert out["queries"][0]["results"][0]["url"] == "https://example.com/a"


def test_research_is_bounded(doc, monkeypatch):
    import tools.web_search as ws

    calls = []
    monkeypatch.setattr(ws, "DDG_AVAILABLE", True)
    monkeypatch.setattr(ws, "web_search", lambda query, max_results=5: calls.append(query) or [])
    many = [Finding(id=f"f{i}", severity=SEVERITY_HIGH, category="c", title=f"t{i}",
                    evidence="e", fixes=[]) for i in range(10)]
    doc.research(many, max_queries=2)
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Triage + full run
# ---------------------------------------------------------------------------
def test_triage_falls_back_when_no_model_answers(doc, monkeypatch):
    class DeadLLM:
        def chat(self, messages, tools=None):
            raise ConnectionError("engine down")

    monkeypatch.setattr(doc, "_doctor_llm", lambda model=None: (DeadLLM(), "nollama/minicpm"))
    findings = [Finding(id="f1", severity=SEVERITY_HIGH, category="c", title="engine down",
                        evidence="no response", fixes=["start the engine"])]
    out = doc.triage(findings)
    assert out["used_model"] is False
    assert out["model"] == "nollama/minicpm"
    assert "start the engine" in out["management_plan"][0]
    assert out["summary"]


def test_triage_uses_the_model_when_it_answers(doc, monkeypatch):
    class LLM:
        def __init__(self):
            self.prompt = ""

        def chat(self, messages, tools=None):
            self.prompt = messages[0]["content"]
            class R:
                content = "WHAT WENT WRONG: the KV pool was too small."
            return R()

    llm = LLM()
    monkeypatch.setattr(doc, "_doctor_llm", lambda model=None: (llm, "nollama/minicpm"))
    findings = [Finding(id="f1", severity=SEVERITY_HIGH, category="c", title="generation failed",
                        evidence="unfinished GenerationStatus", fixes=[])]
    out = doc.triage(findings)
    assert out["used_model"] is True
    assert "KV pool" in out["summary"]
    # The prompt must be small and structured enough for a 1B model.
    assert "WHAT WENT WRONG" in llm.prompt
    assert len(llm.prompt) < 2000


def test_triage_treats_a_refusal_as_no_model(doc, monkeypatch):
    class LLM:
        def chat(self, messages, tools=None):
            class R:
                content = "nollama error (base_url=http://localhost:8010/v1): connection refused"
            return R()

    monkeypatch.setattr(doc, "_doctor_llm", lambda model=None: (LLM(), "nollama/minicpm"))
    out = doc.triage([Finding(id="f", severity=SEVERITY_HIGH, category="c", title="t", evidence="e", fixes=["fix"])])
    assert out["used_model"] is False
    assert "did not answer" in out["note"]


def test_triage_reports_no_findings_cleanly(doc, monkeypatch):
    monkeypatch.setattr(doc, "_doctor_llm", lambda model=None: (None, ""))
    out = doc.triage([])
    assert out["used_model"] is False
    assert "healthy" in out["summary"].lower()


def test_full_run_writes_json_and_markdown(doc, monkeypatch):
    monkeypatch.setattr(doc, "_doctor_llm", lambda model=None: (None, ""))
    monkeypatch.setattr(doc, "collect", lambda **kw: signals())
    report = doc.run(use_llm=False, ask_internet=False, save=True)

    assert report["status"] == "ok"
    assert report["worst_severity"] == SEVERITY_INFO
    assert report["counts"]["high"] == 0
    path = report["path"]
    assert path.endswith(".md")
    md = doc.reports_dir / f"{report['id']}.md"
    js = doc.reports_dir / f"{report['id']}.json"
    assert md.exists() and js.exists()
    assert "# Hermus Doctor Report" in md.read_text(encoding="utf-8")
    assert json.loads(js.read_text(encoding="utf-8"))["id"] == report["id"]
    assert doc.recent(5)[0]["id"] == report["id"]


def test_full_run_report_is_complete_without_a_model(doc, monkeypatch):
    engines = signals()["engines"]
    engines["status"] = "needs_model"
    monkeypatch.setattr(doc, "collect", lambda **kw: signals(engines=engines))
    monkeypatch.setattr(doc, "_doctor_llm", lambda model=None: (None, ""))
    report = doc.run(use_llm=False, ask_internet=False)
    assert report["status"] == "attention"
    assert report["triage"]["used_model"] is False
    assert report["triage"]["management_plan"], "a report with no plan is useless"
    md = to_markdown(report)
    assert "What went wrong and how to manage it" in md
    assert "Findings" in md


def test_run_feeds_the_lessons_loop(doc, monkeypatch):
    engines = signals()["engines"]
    engines["status"] = "needs_model"
    monkeypatch.setattr(doc, "collect", lambda **kw: signals(engines=engines))
    monkeypatch.setattr(doc, "_doctor_llm", lambda model=None: (None, ""))
    added = []
    import core.reasoning.lessons as lessons_mod

    monkeypatch.setattr(lessons_mod.lessons_store, "add",
                        lambda lesson, category="general", **kw: added.append((lesson, category)) or {"success": True})
    doc.run(use_llm=False, ask_internet=False, save=False)
    assert added, "the top finding must become a lesson"
    assert added[0][1].startswith("doctor:")


def test_auto_triage_respects_cooldown_and_cap(doc, monkeypatch):
    monkeypatch.setattr(doc, "collect", lambda **kw: signals())
    monkeypatch.setattr(doc, "_doctor_llm", lambda model=None: (None, ""))
    from core.config import config

    monkeypatch.setattr(config, "doctor_auto", True, raising=False)
    monkeypatch.setattr(config, "doctor_cooldown_minutes", 60, raising=False)
    monkeypatch.setattr(config, "doctor_daily_cap", 2, raising=False)

    first = doc.run(auto=True, use_llm=False, ask_internet=False, save=False)
    assert first["status"] == "ok"
    second = doc.run(auto=True, use_llm=False, ask_internet=False, save=False)
    assert second["status"] == "skipped", "cooldown must stop a runaway self-repair loop"
    assert "cooldown" in second["reason"]


def test_auto_triage_is_off_by_default(doc, monkeypatch):
    monkeypatch.setattr(doc, "collect", lambda **kw: signals())
    from core.config import config

    monkeypatch.setattr(config, "doctor_auto", False, raising=False)
    assert doc.run(auto=True, save=False)["status"] == "skipped"


def test_status_is_cheap_and_shaped_for_the_dashboard(doc, monkeypatch):
    monkeypatch.setattr(doc, "collect", lambda **kw: signals())
    monkeypatch.setattr(doc, "_doctor_llm", lambda model=None: (None, "nollama/minicpm"))
    st = doc.status()
    for key in ("enabled", "auto", "ask_internet", "model", "engine_status",
                "media", "worst_severity", "counts", "finding_count", "stuck", "reports"):
        assert key in st
    assert st["model"] == "nollama/minicpm"


def test_doctor_starts_downloaded_local_engine_instead_of_bailing(doc, monkeypatch):
    """The doctor must start a downloaded MiniCPM model if the engine is off."""
    import core.nollama as nl
    import requests

    started = {}

    def fake_running():
        return False

    def fake_installed():
        return True

    def fake_venv():
        return True

    def fake_best(device="", roles=None):
        return {"id": "minicpm", "path": "/tmp/models/MiniCPM5-1B-int4-g128-ov"}

    def fake_start(**kwargs):
        started.update(kwargs)
        return {"success": True, "pid": 1234}

    monkeypatch.setattr(nl.nollama_manager, "running", fake_running)
    monkeypatch.setattr(nl.nollama_manager, "installed", fake_installed)
    monkeypatch.setattr(nl.nollama_manager, "venv_ready", fake_venv)
    monkeypatch.setattr(nl.nollama_manager, "best_installed_model", fake_best)
    monkeypatch.setattr(nl.nollama_manager, "start", fake_start)
    class _OK:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"data": [{"id": "MiniCPM5-1B-int4-g128-ov"}]}

    monkeypatch.setattr(requests, "get", lambda url, timeout=1.5: _OK())

    assert doc._ensure_local_engine("nollama/MiniCPM5-1B-int4-g128-ov", timeout=3) is True
    assert started, "doctor must start the local engine when MiniCPM is downloaded"


def test_doctor_prefers_downloaded_minicpm_over_ollama(doc, monkeypatch):
    import core.nollama as nl

    monkeypatch.setattr(
        nl.nollama_manager,
        "best_installed_model",
        lambda device="", roles=None: {
            "id": "minicpm",
            "repo": "HarmenWessels/MiniCPM5-1B-int4-g128-ov",
            "path": "/tmp/models/MiniCPM5-1B-int4-g128-ov",
        },
    )
    assert (
        doc._prefer_downloaded_doctor("ollama/llama3.1:8b")
        == "nollama/MiniCPM5-1B-int4-g128-ov"
    )


def test_doctor_falls_back_to_configured_provider_when_local_unavailable(doc, monkeypatch):
    """If the local engine can't run, the doctor still works with any configured key."""
    monkeypatch.setattr(doc, "_prefer_downloaded_doctor", lambda ref: "nollama/MiniCPM5-1B-int4-g128-ov")
    monkeypatch.setattr(doc, "_ensure_local_engine", lambda ref, timeout=30.0: False)
    monkeypatch.setattr(doc, "_configured_doctor_fallback", lambda: ("openrouter/auto", "openrouter"))

    llm, ref = doc._doctor_llm()
    assert ref == "openrouter/auto"
    assert llm is not None
    assert llm.model == "openrouter/auto"
