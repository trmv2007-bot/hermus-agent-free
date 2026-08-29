"""Tests for the skill forge (architecture upgrade A3): verified trajectory →
validated, replayable SKILL.md skill.

Guards the traps that make generated skills dangerous: distilling a failed run,
emitting JSON that is not valid Python, clobbering an existing skill, and
installing something that cannot even be imported.

Offline: no model required (an injected fake LLM stands in for the polish step).
Run: python tests/test_skill_forge.py   (or pytest tests/test_skill_forge.py)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

_TMP = tempfile.mkdtemp(prefix="hermus_forge_")
os.environ["HERMUS_HOME"] = _TMP
os.environ.setdefault("HERMUS_EMBED_BACKEND", "hash")

from core.config import config  # noqa: E402

config.model = "mock/mock"
config.memory2_db_path = str(Path(_TMP) / "memory2.db")
config.memory_db_path = str(Path(_TMP) / "memory.db")
config.trajectory_path = str(Path(_TMP) / "trajectories.jsonl")
config.embeddings_db_path = str(Path(_TMP) / "embeddings.db")
config.skills_dir = str(Path(_TMP) / "skills")

from core import skill_forge as sf  # noqa: E402


def _forge(**kw):
    return sf.SkillForge(skills_dir=str(Path(_TMP) / kw.pop("dir", "skills")), **kw)


GOOD_TRAJ = [
    {"role": "user", "content": "Summarize today's nginx error log and save the report"},
    {"role": "assistant", "content": "I'll read the log, count error classes, then write a report.",
     "tool_calls": [{"name": "shell_execute", "arguments": {"command": "grep -c error /var/log/nginx/error.log"},
                     "id": "c1"}]},
    {"role": "assistant", "content": "Log has 42 errors. Now grouping them.",
     "tool_calls": [{"name": "shell_execute", "arguments": {"command": "awk '{print $3}' /var/log/nginx/error.log | sort | uniq -c | sort -rn | head"},
                     "id": "c2"}]},
    {"role": "assistant", "content": "Top cause is upstream timeouts. Writing report.",
     "tool_calls": [{"name": "write_file", "arguments": {"path": "reports/nginx.md", "content": "# nginx\n42 errors, mostly upstream timeouts"},
                     "id": "c3"}]},
    {"role": "assistant",
     "content": "Report written to reports/nginx.md: 42 errors, dominated by upstream timeouts "
                "with 5 connection resets; recommend raising proxy_read_timeout."},
]
GOOD_RESULTS = [
    {"tool": "shell_execute", "args": {"command": "grep -c error /var/log/nginx/error.log"},
     "result": {"stdout": "42", "returncode": 0, "success": True}},
    {"tool": "shell_execute", "args": {"command": "awk '{print $3}' /var/log/nginx/error.log | sort | uniq -c | sort -rn | head"},
     "result": {"stdout": "30 upstream timed out\n5 connection reset", "returncode": 0, "success": True}},
    {"tool": "write_file", "args": {"path": "reports/nginx.md", "content": "# nginx\n42 errors, mostly upstream timeouts"},
     "result": {"success": True, "path": "reports/nginx.md"}},
]


# --------------------------------------------------------------------------
# Evaluation gate
# --------------------------------------------------------------------------
def test_error_blob_detection_is_structural_not_substring():
    from core.skill_forge import _is_error_blob

    assert _is_error_blob({"error": "boom"}) is True
    assert _is_error_blob({"success": False, "stdout": ""}) is True
    assert _is_error_blob("Traceback (most recent call last):\n  File x") is True
    # a log-analysis command whose *content* mentions errors is not a failed tool call
    assert _is_error_blob({"stdout": "timeout: 42 errors found", "returncode": 0, "success": True}) is False
    assert _is_error_blob({"stdout": "SyntaxError in build.log", "returncode": 0}) is False


def test_single_tool_chat_is_not_a_skill():
    ev = sf.evaluate_trajectory(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello!"}],
        tool_results=[],
    )
    assert ev.harvest is False
    assert any("tool call" in r for r in ev.reasons)


def test_failed_verification_vetoes_harvest_even_with_many_steps():
    traj = [
        {"role": "user", "content": "Summarize the nginx error log and save the report"},
        {"role": "assistant", "content": "working",
         "tool_calls": [{"name": "shell_execute", "arguments": {"command": "ls"}, "id": "a"},
                       {"name": "write_file", "arguments": {"path": "x"}, "id": "b"}]},
        {"role": "assistant", "content": "Done, report saved with the summary of errors."},
    ]
    ev = sf.evaluate_trajectory(traj, verification={"verified": False}, min_tool_calls=2,
                               tool_results=[{"tool": "shell_execute", "result": {"ok": True}},
                                             {"tool": "write_file", "result": {"ok": True}}])
    assert ev.harvest is False
    assert "verification explicitly failed" in " ".join(ev.reasons)


def test_majority_failed_tools_veto_with_reason():
    traj = [
        {"role": "user", "content": "Summarize and file the log report"},
        {"role": "assistant", "content": "trying",
         "tool_calls": [{"name": "shell_execute", "arguments": {"command": "a"}, "id": "1"},
                        {"name": "file_read", "arguments": {"path": "b"}, "id": "2"},
                        {"name": "write_file", "arguments": {"path": "c"}, "id": "3"}]},
        {"role": "assistant", "content": "Report complete with the findings from the log analysis pass."},
    ]
    results = [
        {"tool": "shell_execute", "result": {"error": "exit 1"}},
        {"tool": "file_read", "result": {"error": "not found"}},
        {"tool": "write_file", "result": {"success": True}},
    ]
    ev = sf.evaluate_trajectory(traj, tool_results=results)
    assert ev.harvest is False
    assert "look like failures" in " ".join(ev.reasons)


def test_verified_multi_step_run_is_harvestable():
    ev = sf.evaluate_trajectory(GOOD_TRAJ, verification={"verified": True}, tool_results=GOOD_RESULTS)
    assert ev.harvest is True, ev.to_dict()
    assert ev.score >= 3.0
    assert ev.metrics["tool_calls"] == 3 and ev.metrics["tool_failures"] == 0
    assert "shell_execute" in ev.metrics["unique_tools"]


def test_recovery_from_a_failed_call_is_a_bonus_not_a_block():
    results = [dict(GOOD_RESULTS[0])] + [
        {"tool": "shell_execute", "args": {"command": "bad"}, "result": {"error": "No such file"}},
        {"tool": "shell_execute", "args": {"command": "awk"}, "result": {"stdout": "ok", "returncode": 0}},
    ] + GOOD_RESULTS[1:]
    ev = sf.evaluate_trajectory(GOOD_TRAJ, verification={"verified": True}, tool_results=results)
    assert ev.harvest is True
    assert ev.metrics["recoveries"] >= 1
    assert any("recovered" in r for r in ev.reasons)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def test_extract_steps_flattens_and_marks_errors():
    steps = sf.extract_steps(GOOD_TRAJ, GOOD_RESULTS)
    assert [s.tool for s in steps] == ["shell_execute", "shell_execute", "write_file"]
    assert steps[0].index == 1
    assert steps[0].args["command"].startswith("grep -c")
    assert not any(s.error for s in steps)

    broken = sf.extract_steps(GOOD_TRAJ, [dict(GOOD_RESULTS[0],
                                              result={"error": "grep: no such file"})])
    assert broken[0].error is True


def test_repeated_identical_call_is_collapsed_once():
    traj = [GOOD_TRAJ[0], {
        "role": "assistant", "content": "x",
        "tool_calls": [{"name": "shell_execute", "arguments": {"command": "same"}, "id": "1"},
                       {"name": "shell_execute", "arguments": {"command": "same"}, "id": "2"}]},
        {"role": "assistant", "content": "done"}]
    steps = sf.extract_steps(traj, [])
    assert len(steps) == 1


# --------------------------------------------------------------------------
# Distillation + generation
# --------------------------------------------------------------------------
def test_distill_produces_candidate_with_provenance():
    forge = _forge()
    ev = sf.evaluate_trajectory(GOOD_TRAJ, verification={"verified": True}, tool_results=GOOD_RESULTS)
    steps = sf.extract_steps(GOOD_TRAJ, GOOD_RESULTS)
    cand = forge.distill("Summarize nginx error log and file a report", steps, ev, session_id="s1")
    assert cand.name and cand.name == sf.slugify(cand.name)
    assert cand.steps and cand.verification
    assert cand.provenance.get("hash") and cand.provenance.get("session") == "s1"
    assert "shell_execute" in json.dumps(cand.to_dict())


def test_llm_polish_overrides_mechanical_fields():
    payload = {
        "name": "nginx_log_digest",
        "title": "Digest the nginx error log",
        "description": "Count and group nginx errors, then file a short report.",
        "when_to_use": "Any time an ops log needs a one-paragraph summary.",
        "inputs": ["log_path"],
        "tags": ["ops", "logs"],
        "verification": "reports/nginx.md exists and mentions the error count",
    }
    forge = _forge(llm=lambda messages: "```json\n" + json.dumps(payload) + "\n```")
    ev = sf.evaluate_trajectory(GOOD_TRAJ, verification={"verified": True}, tool_results=GOOD_RESULTS)
    steps = sf.extract_steps(GOOD_TRAJ, GOOD_RESULTS)
    cand = forge.distill("Summarize nginx error log", steps, ev, session_id="s2")
    assert cand.name == "nginx_log_digest"
    assert cand.title == "Digest the nginx error log"
    assert "log_path" in cand.inputs
    # the model never writes code: steps stay the machine-readable truth
    assert [s.tool for s in cand.steps] == [s.tool for s in steps]


def test_bad_llm_json_is_ignored_not_fatal():
    forge = _forge(llm=lambda messages: "sorry, I cannot produce JSON")
    ev = sf.evaluate_trajectory(GOOD_TRAJ, verification={"verified": True}, tool_results=GOOD_RESULTS)
    cand = forge.distill("Summarize nginx error log", sf.extract_steps(GOOD_TRAJ, GOOD_RESULTS), ev)
    assert cand.name and cand.steps


def test_skill_md_has_frontmatter_and_replayable_procedure():
    forge = _forge()
    ev = sf.evaluate_trajectory(GOOD_TRAJ, verification={"verified": True}, tool_results=GOOD_RESULTS)
    cand = forge.distill("Summarize nginx error log", sf.extract_steps(GOOD_TRAJ, GOOD_RESULTS), ev)
    doc = forge.skill_md(cand)
    assert doc.startswith("---")
    assert f"name: {cand.name}" in doc
    assert "description:" in doc and "when_to_use" in doc
    assert "shell_execute" in doc and "nginx" in doc.lower()


def test_generated_skill_py_is_valid_python_and_replayable():
    """The old bug: embedding json.dumps output as source → NameError on `false`."""
    import ast
    import importlib.util

    forge = _forge()
    ev = sf.evaluate_trajectory(GOOD_TRAJ, verification={"verified": True}, tool_results=GOOD_RESULTS)
    cand = forge.distill("Summarize nginx error log", sf.extract_steps(GOOD_TRAJ, GOOD_RESULTS), ev)
    code = forge.skill_py(cand)
    ast.parse(code)  # must compile

    path = Path(_TMP) / "skills_gen" / f"{cand.name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code)
    spec = importlib.util.spec_from_file_location(f"gen_{cand.name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plan = mod.plan()
    assert isinstance(plan, list) and plan and plan[0]["tool"] == "shell_execute"
    assert isinstance(plan[0]["args"], dict)
    dry = mod.run(task="demo", execute=False)
    assert dry["dry_run"] is True and len(dry["steps"]) == len(plan)
    assert dry["success"] is True and dry["skill"] == cand.name
    live = mod.run(task="demo", overrides={"shell_execute": {"command": "echo replayed"}})
    assert isinstance(live, dict) and "steps" in live


# --------------------------------------------------------------------------
# Install / validate / quarantine
# --------------------------------------------------------------------------
def _candidate(forge, name="nginx_log_digest", description=None):
    ev = sf.evaluate_trajectory(GOOD_TRAJ, verification={"verified": True}, tool_results=GOOD_RESULTS)
    cand = forge.distill("Summarize nginx error log and file a report",
                         sf.extract_steps(GOOD_TRAJ, GOOD_RESULTS), ev, session_id="sX")
    cand.name = name
    if description:
        cand.description = description
    return cand


def test_install_writes_skill_and_registers_it():
    forge = _forge(dir="inst")
    cand = _candidate(forge)
    out = forge.install(cand)
    assert out["installed"] is True, out
    root = Path(forge.skills_dir) / cand.name
    assert (root / "SKILL.md").exists() and (root / "skill.py").exists()
    assert (root / "test_skill.py").exists()
    reg = forge.index()
    assert cand.name in reg["skills"]
    entry = reg["skills"][cand.name]
    assert entry["status"] == "active" and entry["version"] == 1
    assert entry["path"].endswith(cand.name)
    assert entry["hash"] and "shell_execute" in entry["tools"]


def test_validate_rejects_a_broken_skill():
    forge = _forge(dir="broken")
    cand = _candidate(forge, "broken_skill")
    forge.install(cand, validate=False)
    root = Path(forge.skills_dir) / cand.name
    (root / "skill.py").write_text("def plan(:\n    ???\n")
    report = forge.validate(root)
    assert report["valid"] is False
    assert "compile" in report.get("error", "").lower() or "skill.py" in report.get("error", "")


def test_smoke_failure_is_quarantined_not_shipped():
    forge = _forge(dir="quar")
    cand = _candidate(forge, "quarantined_skill")
    real = forge.validate

    def always_bad(path, **kw):
        return {"ok": False, "error": "smoke test failed: simulated", "problems": ["simulated"]}

    forge.validate = always_bad
    try:
        out = forge.install(cand)
    finally:
        forge.validate = real
    assert out["installed"] is False
    qdir = Path(out["quarantined"])
    assert qdir.exists() and qdir.parent.name == ".quarantine"
    assert "simulated" in (qdir / "VALIDATION_ERROR.txt").read_text()
    # a quarantined skill must not be listed as available
    assert cand.name not in forge.index()["skills"]
    assert forge.stats()["quarantined"] >= 1


def test_reinstall_of_the_same_run_refreshes_in_place_and_bumps_version():
    forge = _forge(dir="atomic")
    cand = _candidate(forge, "atomic_skill")
    forge.install(cand)
    path = Path(forge.skills_dir) / cand.name / "SKILL.md"
    first_doc = path.read_text()
    cand.description = cand.description + " (revised wording)"
    second = forge.install(cand)          # same provenance hash → refresh, not fork
    assert second["installed"] is True and second["name"] == cand.name
    assert path.read_text() != first_doc
    assert forge.index()["skills"][cand.name]["version"] == 2
    # a genuinely different procedure keeps both instead of clobbering knowledge
    other = _candidate(forge, "atomic_skill")
    other.provenance = dict(other.provenance, hash="deadbeef0000")
    third = forge.install(other)
    assert third["installed"] is True and third["name"] != cand.name
    assert len(list(Path(forge.skills_dir).glob("atomic_skill*"))) >= 2


# --------------------------------------------------------------------------
# Dedupe + full harvest pipeline
# --------------------------------------------------------------------------
def test_unverified_success_is_not_distilled():
    """A run whose work was never verified must not become a learned skill."""
    forge = _forge(dir="unverified")
    out = forge.harvest("Summarize nginx error log and file a report", GOOD_TRAJ,
                        verification={"verified": False}, tool_results=GOOD_RESULTS,
                        session_id="s0")
    assert out["created"] is False
    assert out["stage"] == "unverified"
    assert "verification" in out["reason"].lower()
    # even a run that merely *described* the work is vetoed
    described = [
        {"role": "user", "content": "Summarize nginx error log"},
        {"role": "assistant", "content": "I would read the log and write a report … but no_evidence_of_work "
                                         "means this stage never actually ran the tools.",
         "tool_calls": [{"name": "shell_execute", "arguments": {"command": "true"}},
                        {"name": "write_file", "arguments": {"path": "r.md"}},
                        {"name": "file_read", "arguments": {"path": "r.md"}}]},
    ]
    out2 = forge.harvest("Summarize nginx error log", described, verification={"verified": True},
                         tool_results=[{"tool": "shell_execute", "result": {"ok": True}},
                                       {"tool": "write_file", "result": {"ok": True}},
                                       {"tool": "file_read", "result": {"ok": True}}],
                         session_id="s0b")
    assert out2["created"] is False
    assert out2["stage"] == "unverified"
    assert "no_evidence_of_work" in out2["reason"]


def test_single_success_waits_for_a_repeat():
    """One successful run is a hypothesis, not a skill (repeatability gate)."""
    forge = _forge(dir="repeat")
    first = forge.harvest("Summarize nginx error log and file a report", GOOD_TRAJ,
                          verification={"verified": True}, tool_results=GOOD_RESULTS,
                          session_id="s1")
    assert first["created"] is False
    assert first["stage"] == "awaiting_repeat"
    assert first["observed"] == 1 and first["required"] >= 2
    # the same session repeating itself is NOT independent evidence
    again = forge.harvest("Summarize nginx error log and file a report", GOOD_TRAJ,
                          verification={"verified": True}, tool_results=GOOD_RESULTS,
                          session_id="s1")
    assert again["stage"] == "awaiting_repeat"
    assert again["observed"] == 2 or again["observed"] == 1
    # an independent session confirms the procedure
    second = forge.harvest("Summarize nginx error log and file a report", GOOD_TRAJ,
                           verification={"verified": True}, tool_results=GOOD_RESULTS,
                           session_id="s2")
    assert second["created"] is True, second
    assert second["repeatability"]["observed"] >= second["repeatability"]["required"]


def test_duplicate_goal_merges_instead_of_multiplying():
    forge = _forge(dir="dedupe")
    # repeatability gate: two independent successes before the skill is installed
    seed = forge.harvest("Summarize nginx error log and file a report", GOOD_TRAJ,
                         verification={"verified": True}, tool_results=GOOD_RESULTS, session_id="s1")
    a = forge.harvest("Summarize nginx error log and file a report", GOOD_TRAJ,
                      verification={"verified": True}, tool_results=GOOD_RESULTS, session_id="s2")
    assert seed["stage"] == "awaiting_repeat"
    assert a["created"] is True, a
    b = forge.harvest("Summarize nginx error log and file a report", GOOD_TRAJ,
                      verification={"verified": True}, tool_results=GOOD_RESULTS, session_id="s3")
    assert b["created"] is False
    assert b["stage"] == "dedupe"
    assert b["merged_into"] == a["name"]
    assert b["similarity"] and b["similarity"] >= 0.7


def test_unrelated_goal_becomes_its_own_skill():
    forge = _forge(dir="dedupe2")
    # same forge dir as the dedupe test would also prove cross-skill dedupe, so keep it isolated
    other_traj = [
        {"role": "user", "content": "Renew the TLS certificate for the api host"},
        {"role": "assistant", "content": "checking",
         "tool_calls": [{"name": "shell_execute", "arguments": {"command": "certbot certificates"}, "id": "0"},
                        {"name": "shell_execute", "arguments": {"command": "certbot renew"}, "id": "1"},
                        {"name": "shell_execute", "arguments": {"command": "systemctl reload nginx"}, "id": "2"}]},
        {"role": "assistant", "content": "Certificate renewed and nginx reloaded; expiry is now 90 days out."},
    ]
    tools = [{"tool": "shell_execute", "result": {"stdout": "certs listed"}},
             {"tool": "shell_execute", "result": {"stdout": "renewed"}},
             {"tool": "shell_execute", "result": {"stdout": "reloaded"}}]
    first = forge.harvest("Renew TLS certificate for api host", other_traj,
                          verification={"verified": True}, tool_results=tools, session_id="s9")
    assert first["stage"] == "awaiting_repeat"
    res = forge.harvest("Renew TLS certificate for api host", other_traj,
                        verification={"verified": True}, tool_results=tools, session_id="s10")
    assert res["created"] is True, res


def test_harvest_veto_reports_the_reason():
    forge = _forge(dir="veto")
    out = forge.harvest("what time is it", [{"role": "user", "content": "what time is it"},
                                            {"role": "assistant", "content": "12:00"}],
                        tool_results=[], dry_run=True)
    assert out["created"] is False
    assert out["stage"] == "evaluation"
    assert out["evaluation"]["harvest"] is False


def test_harvest_dry_run_previews_without_writing():
    forge = _forge(dir="dry")
    out = forge.harvest("Summarize nginx error log and file a report", GOOD_TRAJ,
                        verification={"verified": True}, tool_results=GOOD_RESULTS, dry_run=True)
    assert out["created"] is False and out["stage"] == "dry_run"
    assert out["skill_md"].startswith("---")
    assert not (Path(forge.skills_dir) / out["candidate"]["name"]).exists()


def test_run_executes_installed_skill_and_records_outcome():
    forge = _forge(dir="run")
    forge.harvest("Summarize nginx error log and file a report", GOOD_TRAJ,
                  verification={"verified": True}, tool_results=GOOD_RESULTS, session_id="r1")
    made = forge.harvest("Summarize nginx error log and file a report", GOOD_TRAJ,
                         verification={"verified": True}, tool_results=GOOD_RESULTS, session_id="r2")
    assert made["created"] is True, made
    name = made["name"]
    dry = forge.run(name, task="today", execute=False)
    assert dry["success"] is True and dry["dry_run"] is True, dry
    assert dry["steps"][0]["tool"] == "shell_execute"
    st = forge.stats()
    assert st["registered_skills"] >= 1 and st["harvested"] >= 1
    rec = forge.record_outcome(name, True, note="ran in CI")
    assert rec["recorded"] is True
    assert forge.stats()["recent_outcome_rate"] > 0
    entry = forge.index()["skills"][name]
    assert entry["runs"] >= 2 and entry["successes"] >= 2


def test_find_similar_uses_summary_not_whole_document():
    """Jaccard against a long SKILL.md always looks dissimilar; compare summaries."""
    forge = _forge(dir="dedupe")
    assert forge.find_similar("summarize nginx error log and file a report") is not None
    assert forge.find_similar("Completely different: rotate the SSH signing keys for the vault") is None


def test_slugs_are_safe_filenames():
    assert sf.slugify("../../etc/passwd") .count("/") == 0
    assert sf.slugify("Two  Words!!").startswith("two_words")
    assert len(sf.slugify("x" * 300)) <= 44


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
