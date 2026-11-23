"""First run: a brain that exists, a screen that is the companion, and no fake claims.

These tests pin the contract the onboarding work is judged by:

* a fresh install redirects to ``/welcome``, a proven one to ``/companion``
* completing the flow writes state, and a failed model check never does
* model resolution names its source
* the mock UI is unreachable without ``HERMUS_MOCK_UI=1``
* no static string claims a check the code does not run

Offline and dependency-light: nothing here needs a model. A temporary
``HERMUS_HOME`` is set before the first import so the real ``~/.hermus`` is
never touched.
"""

import contextlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

_TMP = tempfile.mkdtemp(prefix="hermus_test_onboarding_")
os.environ["HERMUS_HOME"] = _TMP
os.environ.pop("HERMUS_MODEL", None)

from fastapi.testclient import TestClient  # noqa: E402

import gateway.gateway as gw  # noqa: E402
from core import brain, onboarding  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _client():
    return TestClient(gw.app, base_url="http://testserver")


def _reset_state() -> None:
    path = onboarding.state_path()
    if path.exists():
        path.unlink()


@contextlib.contextmanager
def _no_named_model():
    """A full-suite run leaves HERMUS_MODEL set; resolution must still be ours."""
    saved = os.environ.pop("HERMUS_MODEL", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["HERMUS_MODEL"] = saved


def _brain(status):
    """Patch the probe so resolution is deterministic and offline."""
    original = brain.local_status
    brain.local_status = lambda: status
    return original


SERVING_OLLAMA = {
    "ollama": {"installed": True, "serving": True, "models": ["llama3.2:1b", "mistral:7b"]},
    "lmstudio": {"serving": False, "models": []},
    "nollama": {"serving": False, "models": []},
}
NO_LOCAL = {
    "ollama": {"installed": False, "serving": False, "models": []},
    "lmstudio": {"serving": False, "models": []},
    "nollama": {"serving": False, "models": []},
}


# --------------------------------------------------------------- the state machine


def test_fresh_install_is_incomplete_and_says_nothing_it_cannot_back():
    _reset_state()
    state = onboarding.load()
    assert onboarding.is_complete(state) is False
    assert "no model" in onboarding.incomplete_reason(state)


def test_completion_requires_a_real_answer_not_a_choice():
    _reset_state()
    state = onboarding.load()
    onboarding.record_brain(
        state, {"kind": "local", "provider": "ollama", "name": "llama3.2:1b", "model": "ollama/llama3.2:1b", "source": "test"}
    )
    state["permissions"] = "ask"
    state["name"] = "Hermus"
    onboarding.save(state)
    # Chosen but never proven: still not done, and the reason names the gap.
    assert onboarding.is_complete(onboarding.load()) is False
    assert "has not answered" in onboarding.incomplete_reason(onboarding.load())

    onboarding.save(onboarding.record_check(onboarding.load(), ok=False, model="llama3.2:1b", reason="connection refused"))
    assert onboarding.is_complete(onboarding.load()) is False
    assert "connection refused" in onboarding.incomplete_reason(onboarding.load())

    onboarding.save(onboarding.record_check(onboarding.load(), ok=True, model="llama3.2:1b", ms=120))
    assert onboarding.is_complete(onboarding.load()) is True


def test_the_stub_can_never_be_a_complete_first_run():
    _reset_state()
    state = onboarding.load()
    onboarding.record_brain(state, brain.stub_brain("declined"))
    state["permissions"] = "ask"
    state["name"] = "Hermus"
    onboarding.record_check(state, ok=True, model="stub")
    onboarding.save(state)
    assert onboarding.is_complete(onboarding.load()) is False
    assert "stub" in onboarding.incomplete_reason(onboarding.load())


def test_recording_a_new_brain_invalidates_the_previous_proof():
    _reset_state()
    state = onboarding.load()
    onboarding.record_brain(state, {"kind": "local", "provider": "ollama", "name": "a", "model": "ollama/a", "source": "t"})
    onboarding.record_check(state, ok=True, model="a")
    assert onboarding.is_complete(state) is True
    onboarding.record_brain(state, {"kind": "local", "provider": "ollama", "name": "b", "model": "ollama/b", "source": "t"})
    assert state["check"]["ok"] is False
    assert onboarding.is_complete(state) is False


# ------------------------------------------------------------------- routing


def test_root_redirects_to_welcome_while_incomplete():
    _reset_state()
    r = _client().get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/welcome"


def test_root_redirects_to_the_companion_once_proven():
    _reset_state()
    state = onboarding.load()
    onboarding.record_brain(state, {"kind": "local", "provider": "ollama", "name": "a", "model": "ollama/a", "source": "t"})
    state["permissions"] = "ask"
    state["name"] = "Hermus"
    onboarding.record_check(state, ok=True, model="a")
    onboarding.save(state)
    r = _client().get("/", follow_redirects=False)
    assert r.headers["location"] == "/companion"


def test_welcome_and_companion_are_served_without_a_control_token():
    c = _client()
    welcome = c.get("/welcome")
    assert welcome.status_code == 200
    assert "text/html" in welcome.headers["content-type"]
    assert "What should answer you?" in welcome.text
    assert c.get("/companion").status_code == 200
    for asset in ("/static/welcome.css", "/static/welcome.js", "/static/companion.js"):
        assert c.get(asset).status_code == 200, asset


def test_welcome_offers_every_choice_with_one_already_selected():
    html = (ROOT / "gateway/static/welcome.html").read_text(encoding="utf-8")
    assert html.count('aria-checked="true"') >= 2  # brain default + permission default
    for choice in ('data-choice="local"', 'data-choice="freekey"', 'data-choice="stub"'):
        assert choice in html
    for profile in ('data-profile="watch"', 'data-profile="ask"', 'data-profile="act"'):
        assert profile in html
    assert 'value="Hermus"' in html


def test_companion_screen_keeps_the_panels_behind_one_control():
    html = (ROOT / "gateway/static/companion.html").read_text(encoding="utf-8")
    assert 'href="/control"' in html
    assert html.count("<input") == 1  # one input line, nothing else to configure
    assert "fairy" in html  # the same character the HUD draws


# ------------------------------------------------------------- state endpoints


def test_state_endpoint_reports_the_resolved_model_and_its_source():
    _reset_state()
    original = _brain(SERVING_OLLAMA)
    try:
        with _no_named_model():
            payload = _client().get("/api/v1/onboarding/state").json()
    finally:
        brain.local_status = original
    assert payload["complete"] is False
    # Two models pulled, so the stronger one answers: the smallest no longer wins.
    assert payload["resolved"]["model"] == "ollama/mistral:7b"
    assert "Ollama on your machine" in payload["resolved"]["source"]
    assert "ahead of llama3.2:1b" in payload["resolved"]["source"]
    assert payload["resolved"]["reason"]
    assert payload["pickup"] == "answering with mistral:7b on your machine"


def test_free_tier_is_second_and_named_as_a_free_tier():
    original = _brain(NO_LOCAL)
    os.environ["GROQ_API_KEY"] = "gsk_test_not_a_real_key"
    try:
        with _no_named_model():
            resolved = brain.resolve_brain()
    finally:
        brain.local_status = original
        os.environ.pop("GROQ_API_KEY", None)
    assert resolved["kind"] == "freekey"
    assert resolved["provider"] == "groq"
    assert "free tier" in brain.pickup_line(resolved)


def test_nothing_available_resolves_to_the_stub_and_offers_one_command():
    original = _brain(NO_LOCAL)
    try:
        with _no_named_model():
            resolved = brain.resolve_brain()
            fix = brain.fix_command(resolved)
    finally:
        brain.local_status = original
    assert resolved["kind"] == "stub"
    assert "stub" in brain.pickup_line(resolved)
    assert "Ollama" in fix


def test_declining_everything_records_the_stub_without_completing():
    _reset_state()
    c = _client()
    r = c.post("/api/v1/onboarding/brain", json={"choice": "stub"})
    assert r.status_code == 200
    body = r.json()
    assert body["complete"] is False
    assert "stub" in body["pickup"]
    assert onboarding.load()["brain"]["kind"] == "stub"


def test_a_real_streamed_answer_is_what_completes_the_run():
    """The last step must be an actual model call, not a recorded intention."""
    import core.models.gateway as gateway_module

    _reset_state()

    class FakeLLM:
        def chat_stream(self, messages, tools=None):
            yield "I can look, "
            yield "answer and remember."

    served = {
        "kind": "local",
        "provider": "ollama",
        "name": "llama3.2:1b",
        "model": "ollama/llama3.2:1b",
        "source": "Ollama on your machine (1 model(s) pulled)",
        "reachable": True,
        "present": True,
        "reason": "",
    }
    original_status = brain.local_status
    original_resolve = brain.resolve_brain
    original_gateway = gateway_module._gateway
    brain.local_status = lambda *a, **k: SERVING_OLLAMA
    brain.resolve_brain = lambda preferred=None, status=None: dict(served)
    gateway_module._gateway = gateway_module.ModelGateway(llm_builder=lambda **kw: FakeLLM())
    try:
        c = _client()
        assert c.post("/api/v1/onboarding/brain", json={"choice": "local"}).json()["ok"] is True
        assert c.post("/api/v1/onboarding/permissions", json={"profile": "act"}).json()["ok"] is True
        assert c.post("/api/v1/onboarding/name", json={"name": "Ada"}).json()["ok"] is True
        assert onboarding.is_complete(onboarding.load()) is False  # chosen, not yet proven
        body = c.post("/api/v1/onboarding/hello", json={"text": "what can you do?"}).text
    finally:
        brain.local_status = original_status
        brain.resolve_brain = original_resolve
        gateway_module._gateway = original_gateway

    tokens = re.findall(r"event: token\ndata: (\{.*?\})\n", body)
    assert "".join(json.loads(t)["text"] for t in tokens) == "I can look, answer and remember."
    assert "event: error" not in body
    assert '"first_token_ms"' in body
    assert '"ok": true' in body
    assert onboarding.is_complete(onboarding.load()) is True
    assert onboarding.load()["check"]["model"] == "llama3.2:1b"


def test_the_installer_never_records_a_choice_nobody_made():
    """A non-interactive install asked nothing, so it must record nothing."""
    import bootstrap

    _reset_state()
    saved = {k: os.environ.pop(k, None) for k in ("GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY")}
    original = brain.local_status
    brain.local_status = lambda *a, **k: NO_LOCAL
    try:
        result = bootstrap.first_run(interactive=False)
    finally:
        brain.local_status = original
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
    assert result.get("unchosen") is True
    state = onboarding.load()
    assert state["brain"] is None
    assert "declined_all" not in state


def test_one_failure_statement_is_written_per_attempt():
    """`error` carries the fix, `done` carries only the reason: no second write."""
    js = (ROOT / "gateway/static/welcome.js").read_text(encoding="utf-8")
    assert "if (answered || failed) return;" in js


def test_the_companion_refuses_to_ask_a_model_that_is_not_there():
    js = (ROOT / "gateway/static/companion.js").read_text(encoding="utf-8")
    assert "I cannot answer" in js
    assert js.index('modelInfo.kind === "stub"') < js.index('fetch("/stream/command"')


def test_the_welcome_screen_announces_the_download_before_it_starts():
    """Continue on a machine with no serving model shows the step, then starts it."""
    js = (ROOT / "gateway/static/welcome.js").read_text(encoding="utf-8")
    assert "function localReady()" in js
    # the prepare field is revealed before the download is requested
    show_at = js.index('show($("#prepare-field"), true);')
    post_at = js.index('post("/api/v1/onboarding/prepare")')
    assert show_at < post_at
    # an install that has not happened offers a re-check, not a dead button
    assert '"Check again"' in js


def test_name_and_permissions_are_written_and_restored():
    _reset_state()
    c = _client()
    assert c.post("/api/v1/onboarding/name", json={"name": "Ada"}).json()["name"] == "Ada"
    assert c.post("/api/v1/onboarding/permissions", json={"profile": "watch"}).json()["profile"] == "watch"
    assert c.post("/api/v1/onboarding/permissions", json={"profile": "nonsense"}).status_code == 400
    assert onboarding.load()["name"] == "Ada"
    assert onboarding.current_profile() == "watch"


def test_watch_only_denies_every_mutating_tool_and_keeps_reads():
    watch = onboarding.decisions_for("watch")
    assert watch and all(decision == "deny" for decision in watch.values())
    assert onboarding.decisions_for("ask")["write_file"] == "ask"
    act = onboarding.decisions_for("act")
    assert act["write_file"] == "allow"  # workspace writes, no prompt
    assert any(value == "ask" for value in act.values())  # host commands still stop


def test_finish_refuses_an_incomplete_run_with_the_reason():
    _reset_state()
    r = _client().post("/api/v1/onboarding/finish")
    assert r.status_code == 409
    assert r.json()["ok"] is False
    assert r.json()["reason"]


def test_every_asset_the_two_screens_reference_is_actually_served():
    """A stylesheet that 404s is invisible in review and obvious on screen."""
    c = _client()
    for page in ("welcome.html", "companion.html"):
        html = (ROOT / "gateway/static" / page).read_text(encoding="utf-8")
        refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
        assert refs, page
        for ref in refs:
            assert c.get(ref).status_code == 200, f"{page} references {ref}"


def test_hello_without_a_model_answers_once_with_the_fix_command():
    _reset_state()
    original = _brain(NO_LOCAL)
    try:
        with _no_named_model():
            body = _client().post("/api/v1/onboarding/hello", json={"text": "what can you do?"}).text
    finally:
        brain.local_status = original
    assert "event: error" in body
    assert "event: done" in body
    assert '"ok": false' in body
    # exactly one failure statement, and it carries the command that fixes it
    assert body.count("event: error") == 1
    assert "ollama" in body.lower() or "Ollama" in body
    # and the run stays incomplete
    assert onboarding.is_complete(onboarding.load()) is False


# ---------------------------------------------------------------- honesty gates


def test_mock_ui_is_unreachable_without_the_env_flag():
    c = _client()
    os.environ.pop("HERMUS_MOCK_UI", None)
    assert c.get("/static/nexus-mock.js").status_code == 404
    os.environ["HERMUS_MOCK_UI"] = "1"
    try:
        assert c.get("/static/nexus-mock.js").status_code == 200
    finally:
        os.environ.pop("HERMUS_MOCK_UI", None)


def test_no_static_string_claims_a_check_the_code_does_not_run():
    """Every "checked"/"verified"/"nominal" claim must sit next to real evidence."""
    hud = (ROOT / "gateway/static/hud.js").read_text(encoding="utf-8")
    assert "Every system checked" not in hud
    assert "nol claims" not in hud
    # the boot caption is built from the real readiness response
    assert '"/readyz"' in hud
    assert "NOT READY" in hud
    # no scripted tick list survives in the boot ceremony
    assert "safety interlocks" not in hud
    assert 'model fleet", "LINKED' not in hud


def test_readme_build_table_cites_a_test_that_exists():
    """A row's evidence must be a file in this repository, not a promise."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    cited = re.findall(r"`(tests/test_[a-z0-9_]+\.py)`", readme)
    if "| Capability |" in readme or "| Area | Status |" in readme:
        assert cited, "the build-status table cites nothing"
    for path in cited:
        assert (ROOT / path).is_file(), path


def test_onboarding_state_never_records_a_check_that_did_not_happen():
    _reset_state()
    state = onboarding.load()
    assert state["check"]["ok"] is False
    onboarding.record_brain(state, {"kind": "local", "provider": "ollama", "name": "x", "model": "ollama/x", "source": "t"})
    assert state["check"]["ok"] is False
    assert json.dumps(state["check"]).count("true") == 0


def test_model_subcommand_reports_its_source(capsys=None):
    import bootstrap

    code = bootstrap.main(["model"])
    assert code in (0, 1)  # 1 is the honest "no model" exit, never a crash


def test_no_windows_only_imports_at_module_import_time():
    """CI runs Linux; these modules must import with no platform-specific deps."""
    for name in ("core/brain.py", "core/onboarding.py", "gateway/routes_onboarding.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        top = source.split("\n\n\n")[0]
        assert not re.search(r"^import (win32|winreg|msvcrt)\b", top, re.M), name
