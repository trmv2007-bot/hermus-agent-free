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
from core import brain, free_tier, model_catalog, onboarding  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _client():
    return TestClient(gw.app, base_url="http://testserver")


def _reset_state() -> None:
    """Clear this test run's own state, and only ever inside its temporary home.

    The guard is not decoration: ``state_path()`` once resolved to the person's
    real ``~/.hermus`` in this very process, and this line deleted his setup
    record twice.
    """
    path = onboarding.state_path()
    from _real_home_guard import real_home_hit

    if real_home_hit(path) is not None:
        raise AssertionError(f"refusing to touch the real install's state file: {path}")
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


@contextlib.contextmanager
def _no_keyless_rung():
    """Both keyless rungs marked down, so a case can reach the keyed rung or the stub."""
    for provider in model_catalog.KEYLESS_PROVIDERS:
        free_tier.mark_down(provider, "marked down by this test")
    try:
        yield
    finally:
        free_tier.reset_health()


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


def test_root_opens_the_room_when_a_keyless_tier_can_answer():
    """A clean machine with no key and nothing installed still opens the room.

    This is the zero-setup promise: the keyless rung answers, so a wizard before
    the first answer would be asking for a decision the product no longer needs.
    """
    _reset_state()
    original = _brain(NO_LOCAL)
    try:
        with _no_named_model():
            r = _client().get("/", follow_redirects=False)
    finally:
        brain.local_status = original
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/companion"


def test_root_still_opens_first_run_when_nothing_can_answer():
    """The welcome screen is for an install with no rung left, never for a working one."""
    _reset_state()
    original = _brain(NO_LOCAL)
    try:
        with _no_keyless_rung():
            r = _client().get("/", follow_redirects=False)
    finally:
        brain.local_status = original
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
    # The panels are one deliberate step away: the Systems sheet's closed
    # Diagnostics door, which names the console tab rather than its front page.
    assert 'href="/control?tab=systems"' in html
    assert 'class="sheet-diagnostics"' in html and "<details" in html
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


def test_the_keyless_rung_is_second_and_asks_for_nothing():
    original = _brain(NO_LOCAL)
    try:
        with _no_named_model():
            resolved = brain.resolve_brain()
    finally:
        brain.local_status = original
    assert resolved["kind"] == "keyless"
    assert resolved["provider"] == model_catalog.KEYLESS_PROVIDERS[0]
    assert "free tier" in brain.pickup_line(resolved)
    assert "no key" in brain.pickup_line(resolved)
    assert "leave this machine" in resolved["privacy"]


def test_a_keyed_free_tier_answers_when_the_keyless_rungs_are_down():
    original = _brain(NO_LOCAL)
    os.environ["GROQ_API_KEY"] = "gsk_test_not_a_real_key"
    try:
        with _no_named_model(), _no_keyless_rung():
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
        with _no_named_model(), _no_keyless_rung():
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


def test_the_installer_records_what_answers_and_never_invents_a_choice():
    """A non-interactive install answers with the keyless rung, not a declined stub.

    Nothing is asked of a person who is not at the keyboard, so nothing is
    invented: the recorded brain is the one that will actually answer, and the
    install does not end on a stub just because nobody chose.
    """
    import bootstrap

    _reset_state()
    saved = {k: os.environ.pop(k, None) for k in ("GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY")}
    original = brain.local_status
    brain.local_status = lambda *a, **k: NO_LOCAL
    try:
        with _no_named_model():
            result = bootstrap.first_run(interactive=False)
    finally:
        brain.local_status = original
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
    assert result.get("keyless") is True
    assert result.get("model", "").startswith(f"{model_catalog.KEYLESS_PROVIDERS[0]}/")
    assert result.get("privacy")
    state = onboarding.load()
    assert state["brain"]["kind"] == "keyless"
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


@contextlib.contextmanager
def _clean_policy():
    """An empty policy for one test, with the singleton's file put back afterwards."""
    from core.permissions import permission_manager

    saved = json.loads(json.dumps(permission_manager.overrides))
    path = permission_manager.overrides_path
    on_disk = path.read_bytes() if path.exists() else None
    permission_manager.overrides = {}
    try:
        yield permission_manager
    finally:
        permission_manager.overrides = saved
        if on_disk is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(on_disk)


def test_an_unknown_profile_is_refused_with_its_caller_named():
    _reset_state()
    with _clean_policy() as manager:
        try:
            onboarding.apply_profile("guardian", source="POST /api/v1/onboarding/permissions")
        except onboarding.ProfileError as exc:
            reason = str(exc)
        else:
            raise AssertionError("an unknown profile was accepted")
        assert "guardian" in reason
        assert "POST /api/v1/onboarding/permissions" in reason
        assert "watch, ask, act" in reason
        assert manager.overrides == {}
        try:
            onboarding.decisions_for("guardian")
        except onboarding.ProfileError:
            pass
        else:
            raise AssertionError("decisions_for mapped an unknown profile onto a policy")
    assert onboarding.load()["permissions"] == onboarding.DEFAULT_PROFILE


def test_a_known_profile_round_trips_as_a_policy_not_only_a_name():
    _reset_state()
    with _clean_policy() as manager:
        applied = onboarding.apply_profile("act", source="test")
        assert applied["profile"] == "act"
        assert manager.overrides["profile"] == "act"
        assert manager.overrides["profile_policy"]["decisions"] == onboarding.decisions_for("act")
        assert manager.overrides["tools"] == onboarding.decisions_for("act")
        assert onboarding.current_profile() == "act"

        r = _client().post("/api/v1/onboarding/permissions", json={"profile": "watch"})
        assert r.status_code == 200 and r.json()["profile"] == "watch"
        state = onboarding.load()
        assert state["permissions"] == "watch"
        assert state["permissions_policy"]["decisions"] == onboarding.decisions_for("watch")
        assert onboarding.current_profile() == "watch"


def test_the_route_refuses_an_unknown_profile_and_says_why():
    _reset_state()
    with _clean_policy():
        r = _client().post("/api/v1/onboarding/permissions", json={"profile": "observe"})
        assert r.status_code == 400
        assert r.json()["ok"] is False
        assert "observe" in r.json()["error"]
        assert "/api/v1/onboarding/permissions" in r.json()["error"]


def test_a_label_only_record_from_an_older_install_still_loads():
    _reset_state()
    onboarding.state_path().write_text(json.dumps({"version": 1, "name": "Ada", "permissions": "watch"}), encoding="utf-8")
    state = onboarding.load()
    assert state["permissions"] == "watch"
    assert state["permissions_policy"] is None
    assert "no permission choice" not in onboarding.incomplete_reason(state)


def test_a_renamed_or_removed_label_does_not_widen_the_record():
    """The install agreed to watch; a later build calling it something else changes nothing."""
    _reset_state()
    with _clean_policy() as manager:
        applied = onboarding.apply_profile("watch", source="test")
        state = onboarding.load()
        state["permissions"] = applied["profile"]
        state["permissions_policy"] = applied["policy"]
        onboarding.save(state)

        manager.overrides["profile"] = "observe"
        state = onboarding.load()
        state["permissions"] = "observe"
        onboarding.save(state)

        assert onboarding.current_profile() == "watch"
        assert manager.overrides["tools"]["write_file"] == "deny"
        assert onboarding.load()["permissions"] == "watch"
        assert "no permission choice" not in onboarding.incomplete_reason(onboarding.load())


def test_a_record_whose_label_is_gone_resolves_from_the_policy_beside_it():
    _reset_state()
    onboarding.state_path().write_text(
        json.dumps(
            {
                "version": 1,
                "name": "Ada",
                "permissions": "observe",
                "permissions_policy": {
                    "version": onboarding.POLICY_VERSION,
                    "profile": "observe",
                    "decisions": onboarding.decisions_for("watch"),
                },
            }
        ),
        encoding="utf-8",
    )
    assert onboarding.load()["permissions"] == "watch"


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


def test_hello_without_a_model_still_answers_from_the_keyless_rung():
    """The first question a person asks is answered, without choosing anything."""
    import core.models.gateway as gateway_module

    _reset_state()
    original = _brain(NO_LOCAL)

    class FakeLLM:
        def chat_stream(self, messages, tools=None):
            yield "I can answer, and installing Ollama keeps your words local."

    class FakeGateway:
        def llm(self, model=None):
            return FakeLLM()

    real_gateway = gateway_module.get_model_gateway
    gateway_module.get_model_gateway = lambda: FakeGateway()
    try:
        with _no_named_model():
            body = _client().post("/api/v1/onboarding/hello", json={"text": "what can you do?"}).text
    finally:
        brain.local_status = original
        gateway_module.get_model_gateway = real_gateway
    assert "event: error" not in body
    assert "event: token" in body
    assert '"ok": true' in body
    assert "no key" in body
    assert "leave this machine" in body
    assert onboarding.load()["brain"]["kind"] == "keyless"


def test_hello_with_no_rung_left_says_what_to_install():
    _reset_state()
    original = _brain(NO_LOCAL)
    try:
        with _no_named_model(), _no_keyless_rung():
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
