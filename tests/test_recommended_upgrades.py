"""Tests for the newly built recommendations across Council, Security, Skills, Memory, and Tools."""
import pytest
from pathlib import Path

def test_scope_checker_whitelisting(tmp_path):
    from pentest.scope import ScopeChecker

    config_path = tmp_path / "scope.json"
    checker = ScopeChecker(str(config_path))
    checker.save({
        "enabled": True,
        "allowed_domains": ["localhost", "127.0.0.1", "*.mycorp.internal"],
        "disallowed_domains": ["bank.com", "pentest-blocked.org"]
    })

    assert checker.is_in_scope("http://localhost:8080") is True
    assert checker.is_in_scope("https://api.mycorp.internal/v1") is True
    assert checker.is_in_scope("https://sub.api.mycorp.internal") is True
    assert checker.is_in_scope("https://evil.bank.com") is False
    assert checker.is_in_scope("https://unauthorized-domain.com") is False

    with pytest.raises(PermissionError):
        checker.validate_or_raise("https://unauthorized-domain.com")


def test_pentest_recon_scope_enforcement(tmp_path, monkeypatch):
    from pentest.recon import subdomain_enum, fingerprinting, attack_surface_mapping
    from pentest.scope import scope_checker

    monkeypatch.setattr(scope_checker, "is_in_scope", lambda target: "allowed" in str(target))

    res_blocked = subdomain_enum("blocked-domain.com")
    assert "out of authorized pentest scope" in res_blocked.get("error", "")

    fp_blocked = fingerprinting("http://blocked-domain.com")
    assert "out of authorized pentest scope" in fp_blocked.get("error", "")

    asm_blocked = attack_surface_mapping("blocked-domain.com")
    assert "out of authorized pentest scope" in asm_blocked.get("error", "")


def test_constitution_amendment_diff():
    from core.counsel.constitution import ConstitutionManager

    mgr = ConstitutionManager()
    prop = mgr.propose({
        "target": "budget",
        "budget_key": "max_rounds",
        "change": "5",
        "reason": "Expand deliberation rounds"
    }, source="test")
    assert prop.get("status") == "pending"
    amendment_id = prop["amendment"]["id"]

    diff_res = mgr.diff(amendment_id)
    assert diff_res["success"] is True
    assert "max_rounds" in diff_res["diff"]

    # Clean up
    mgr.reject(amendment_id)


def test_memory2_hybrid_rrf_and_compaction(tmp_path):
    from core.memory2 import Memory2

    mem = Memory2(str(tmp_path / "test_memory2.db"))
    mem.remember("semantic", "PostgreSQL database indexing with GiST and BTree")
    mem.remember("working", "Review temporary session log buffer", metadata={"s": 1})
    mem.remember("procedural", "Deploy Docker container with docker-compose up -d")

    # Hybrid recall test
    hybrid = mem.hybrid_recall("PostgreSQL database")
    assert len(hybrid) > 0
    assert "rrf_score" in hybrid[0]
    assert hybrid[0]["content"].startswith("PostgreSQL")

    # Compaction test (max_age_hours=0 prunes everything older than right now)
    compact_res = mem.compact_working_memory(max_age_hours=0)
    assert compact_res["deleted_count"] >= 1

    # Ensure semantic/procedural memories were NOT touched
    leftover = mem.recall("Docker")
    assert len(leftover) >= 1


def test_skill_manager_test_generation_and_health(tmp_path):
    from core.skill_manager import SkillManager

    mgr = SkillManager(str(tmp_path / "skills"))
    fake_traj = [
        {"role": "user", "content": "analyze data", "tool_calls": [{"name": "web_read"}]},
        {"role": "assistant", "content": "fetched", "tool_calls": [{"name": "duckduckgo_search"}]},
        {"role": "assistant", "content": "done", "tool_calls": [{"name": "file_write"}]},
    ]
    res = mgr.create_skill_from_trajectory(fake_traj, session_id="test_sess_01")
    assert res["created"] is True
    skill_dir = Path(res["path"])
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "skill.py").exists()
    assert (skill_dir / "test_skill.py").exists()
    assert "def test_" in (skill_dir / "test_skill.py").read_text()

    # Health check for skill
    health = mgr.get_skill_health(res["name"])
    assert health["healthy"] is True
    assert health["consecutive_failures"] == 0


def test_web_read_caching(tmp_path, monkeypatch):
    import tools.internet_eyes as ie

    monkeypatch.setattr(ie, "Path", lambda p: tmp_path / p)
    # Mock requests.get
    class MockResp:
        status_code = 200
        text = "# Cached Page Content\nThis is mock content"
    monkeypatch.setattr(ie.requests, "get", lambda *args, **kwargs: MockResp())

    res1 = ie.web_read("http://example-cached-test.com", use_jina=False, use_cache=True)
    assert res1["success"] is True

    # Call again, should return cached
    res2 = ie.web_read("http://example-cached-test.com", use_jina=False, use_cache=True)
    assert res2["success"] is True
    assert res2.get("cached") is True
