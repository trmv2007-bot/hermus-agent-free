"""Tests for the harness model layer: discovery, spec resolution, ping, json checks.

Resolution tests use a fake gateway (monkeypatched) so they are deterministic;
ping + runner-preflight tests use the real gateway with mock/mock (offline).
"""

from __future__ import annotations

import json

import pytest

from core.harness import models as hm
from core.harness.tasks import resolve_suite
from core.harness.verify import verify_task

# --------------------------------------------------------------------------- fakes


class FakeGateway:
    def __init__(self, rows, resolve_map):
        self._cat = {"models": rows, "providers": []}
        self._resolve = resolve_map

    def catalog(self, probe=False, refresh=False):
        return self._cat

    def resolve_model(self, role="default", **kw):
        return self._resolve.get(role, (None, None))


ROWS = [
    {"ref": "groq/llama-3.3-70b", "provider": "groq", "source": "stored", "available": True, "capabilities": {"tools": "yes"}},
    {"ref": "ollama/llama3.1:8b", "provider": "ollama", "source": "local", "available": True, "capabilities": {}},
]
RESOLVE = {"default": ("groq", "llama-3.3-70b"), "coding": ("ollama", "llama3.1:8b")}


@pytest.fixture
def fake_gateway(monkeypatch):
    gw = FakeGateway(ROWS, RESOLVE)
    monkeypatch.setattr(hm, "_gateway", lambda: gw)
    return gw


@pytest.fixture
def empty_gateway(monkeypatch):
    gw = FakeGateway([], {"default": (None, None)})
    monkeypatch.setattr(hm, "_gateway", lambda: gw)
    return gw


# --------------------------------------------------------------------------- list / resolve


def test_list_models_includes_catalog_and_mock(fake_gateway):
    info = hm.list_models()
    refs = [m["ref"] for m in info["models"]]
    assert "groq/llama-3.3-70b" in refs and "ollama/llama3.1:8b" in refs
    assert hm.MOCK_REF in refs  # offline fallback always present
    ollama = next(m for m in info["models"] if m["provider"] == "ollama")
    assert ollama["local"] is True


def test_resolve_auto_uses_default_role(fake_gateway):
    r = hm.resolve_model_spec("auto")
    assert r["ok"] and r["ref"] == "groq/llama-3.3-70b" and r["validated"] is True


def test_resolve_role_name(fake_gateway):
    r = hm.resolve_model_spec("coding")
    assert r["ok"] and r["ref"] == "ollama/llama3.1:8b"


def test_resolve_exact_ref_and_passthrough(fake_gateway):
    assert hm.resolve_model_spec("groq/llama-3.3-70b")["validated"] is True
    r = hm.resolve_model_spec("groq/some-new-model")
    assert r["ok"] and r["ref"] == "groq/some-new-model" and r["validated"] is False  # attempted directly


def test_resolve_bare_provider(fake_gateway):
    assert hm.resolve_model_spec("groq")["ref"] == "groq/llama-3.3-70b"
    assert hm.resolve_model_spec("ollama")["ref"] == "ollama/llama3.1:8b"


def test_resolve_mock_always_ok():
    for spec in ("mock", "mock/mock", ""):
        r = hm.resolve_model_spec(spec)
        assert r["ok"] and r["ref"] == "mock/mock"


def test_resolve_auto_falls_back_to_mock_when_nothing_configured(empty_gateway):
    r = hm.resolve_model_spec("auto")
    assert r["ok"] and r["ref"] == "mock/mock" and r["source"] == "fallback"
    assert "mock/mock" in r["note"]


def test_resolve_unknown_fails_with_available_list(fake_gateway):
    r = hm.resolve_model_spec("bogus")
    assert not r["ok"] and "groq/llama-3.3-70b" in r["note"]
    r2 = hm.resolve_model_spec("ollama")  # local provider, but has models here
    assert r2["ok"]


def test_resolve_local_provider_without_models_fails(empty_gateway):
    r = hm.resolve_model_spec("ollama")
    assert not r["ok"] and "ollama/<model>" in r["note"]


# --------------------------------------------------------------------------- ping


def test_ping_mock_model_offline():
    out = hm.ping_model("mock/mock", timeout_s=10)
    assert out["ok"] is True and out["latency_ms"] >= 0
    assert out["content"]


# --------------------------------------------------------------------------- json check


def test_json_check_fenced_and_dotted(tmp_path):
    resp = 'Here you go:\n```json\n{"top": "sig_critical", "count": 3, "items": [{"id": "a"}, {"id": "b"}]}\n```'
    v = verify_task(
        {
            "checks": [
                {"type": "json", "key": "top", "value": "sig_critical"},
                {"type": "json", "key": "count", "value": "3"},
                {"type": "json", "key": "items.1.id", "value": "b"},
            ]
        },
        workspace=tmp_path,
        response=resp,
    )
    assert v["success"], v["checks"]


def test_json_check_failures(tmp_path):
    v = verify_task({"checks": [{"type": "json", "key": "nope"}]}, workspace=tmp_path, response='{"a": 1}')
    assert not v["success"] and "missing" in v["checks"][0]["detail"]
    v2 = verify_task({"checks": [{"type": "json", "key": "a"}]}, workspace=tmp_path, response="no json at all")
    assert not v2["success"]
    v3 = verify_task({"checks": [{"type": "json", "key": "a", "value": "zzz"}]}, workspace=tmp_path, response='{"a": 1}')
    assert not v3["success"]


# --------------------------------------------------------------------------- fairy suite


def test_fairy_suite_loads():
    suite = resolve_suite("fairy")
    assert suite.name == "fairy"
    assert len(suite.tasks) == 5
    ids = {t.id for t in suite.tasks}
    assert {"fairy_idle", "fairy_triage", "fairy_json_report", "fairy_summary", "fairy_attention_file"} == ids
    assert "smoke" in resolve_suite("smoke").name


# --------------------------------------------------------------------------- runner integration


def test_runner_resolves_model_and_preflights_mock(tmp_path):
    from core.harness.runner import HarnessRunner, RunOptions

    suite_path = tmp_path / "s.json"
    suite_path.write_text(
        json.dumps(
            {
                "name": "s",
                "tasks": [
                    {"id": "t", "prompt": "Reply with exactly: PING-OK", "checks": [{"type": "substring", "value": "PING-OK"}]}
                ],
            }
        )
    )
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    report = runner.run_suite(str(suite_path), RunOptions(model="mock/mock", timeout_s=60))
    assert report["model"] == "mock/mock"
    assert report["model_spec"] == "mock/mock"
    assert report["model_resolution"]["ok"] is True
    assert report["preflight"]["ok"] is True  # real ping through the gateway
    assert report["tasks"][0]["success"] is True


def test_runner_rejects_unresolvable_model_spec(tmp_path):
    from core.harness.runner import HarnessRunner, RunOptions

    suite_path = tmp_path / "s.json"
    suite_path.write_text(json.dumps({"name": "s", "tasks": [{"id": "t", "prompt": "x", "checks": []}]}))
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    with pytest.raises(ValueError, match="cannot resolve model"):
        runner.run_suite(str(suite_path), RunOptions(model="bogus-provider-xyz"))


# --------------------------------------------------------------------------- CLI


def test_cli_model_subcommands_parse():
    from hermus_cli import COMMANDS, build_parser

    assert "harness" in COMMANDS
    parser = build_parser()
    a = parser.parse_args(["harness", "run", "smoke", "--models", "mock/mock,auto", "--no-ping"])
    assert a.harness_action == "run" and a.models == "mock/mock,auto" and a.no_ping is True
    assert parser.parse_args(["harness", "models", "--probe", "--json"]).harness_action == "models"
    p = parser.parse_args(["harness", "ping", "mock/mock", "--timeout", "5"])
    assert p.harness_action == "ping" and p.model == "mock/mock" and p.timeout == 5
    assert parser.parse_args(["harness", "ping"]).model == "auto"


def test_cli_harness_models_and_ping_run_offline(capsys):
    from hermus_cli import build_parser, g_runtime
    from hermus_cli._common import CLIContext

    parser = build_parser()
    ctx = CLIContext(parser=parser)
    g_runtime._run_harness(parser.parse_args(["harness", "models"]), ctx)
    out = capsys.readouterr().out
    assert "mock/mock" in out and "Discovered models" in out
    g_runtime._run_harness(parser.parse_args(["harness", "ping", "mock/mock", "--timeout", "10"]), ctx)
    out = capsys.readouterr().out
    assert "OK" in out and "mock/mock" in out
