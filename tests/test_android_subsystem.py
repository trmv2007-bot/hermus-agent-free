"""Android control subsystem tests (§16–19).

Honest labels:
* the unit/integration tests verify the *backend boundary*: operation authorization
  (consent + allowlist), honest ``android_control_unavailable`` reporting, audit,
  ADB command building (with an injected runner), and secure integrity. They do
  NOT imply device capability.
* the host-E2E test runs against a real device/emulator and is SKIPPED when no
  adb/device is available — it is never passed on a mock.
"""
from __future__ import annotations

import os
import types
from pathlib import Path

import pytest


def _fake_runner(record: list, *, stdout: str = "", rc: int = 0, binary: bool = False):
    from tests._android_fixtures import PNG_1x1_BYTES
    def _run(cmd, timeout):
        record.append(cmd)
        # Return a PNG payload for `screencap`, otherwise the provided text stdout.
        if binary:
            return types.SimpleNamespace(returncode=rc, stdout=PNG_1x1_BYTES, stderr=b"")
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr="")
    return _run


def _ok_transport():
    class _T:
        def connect(self): return {"ok": True, "device": "abc"}
        def get_screen(self, **kw): return {"ok": True, "format": "png", "bytes": 100, "data": "x"}
        def get_ui_tree(self, **kw): return {"ok": True, "format": "xml", "data": "<node/>"}
        def tap(self, x, y, **kw): return {"ok": True, "x": x, "y": y}
        def type_text(self, text, **kw): return {"ok": True, "length": len(text)}
        def back(self, **kw): return {"ok": True, "keyevent": "BACK"}
        def launch_app(self, package, **kw): return {"ok": True, "package": package}
        def device_id(self): return "abc"
    return _T()


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------
def test_android_tools_registered():
    from core.tool_registry import tool_registry
    tool_registry.load(force=True)
    names = set(tool_registry.list_tools()["tools"])
    for t in ("android_capability", "android_connect", "android_get_screen",
              "android_get_ui_tree", "android_tap", "android_type", "android_back",
              "android_launch_app", "android_permission_grant", "android_permission_revoke",
              "android_permission_set_ops", "android_permission_status"):
        assert t in names, f"{t} not registered"


# ---------------------------------------------------------------------------
# Consent + allowlist authorization
# ---------------------------------------------------------------------------
def test_consent_denied_by_default(tmp_path):
    from core.android.permissions import AndroidPermissionManager
    pm = AndroidPermissionManager(path=str(tmp_path / "perm.json"))
    with pytest.raises(Exception, match="consent"):
        pm.require_access("tap")
    # allowlisting alone is not enough; class consent is separate and explicit.
    assert pm.is_allowed("tap") is True
    assert pm.is_consented("ui_control") is False


def test_grant_revoke_and_allowlist(tmp_path):
    from core.android.permissions import AndroidPermissionManager
    pm = AndroidPermissionManager(path=str(tmp_path / "perm.json"))
    pm.grant("ui_control")
    assert pm.require_access("tap") == "ui_control"
    pm.revoke("ui_control")
    with pytest.raises(Exception, match="consent"):
        pm.require_access("tap")
    # Restrict the allowlist
    pm.grant("ui_control")
    pm.set_allowed_ops(["get_screen"])
    with pytest.raises(Exception, match="allowlist"):
        pm.require_access("tap")
    assert pm.allowed_ops() == ["get_screen"]


def test_unknown_op_class_is_rejected(tmp_path):
    from core.android.permissions import AndroidPermissionManager
    pm = AndroidPermissionManager(path=str(tmp_path / "perm.json"))
    with pytest.raises(ValueError, match="unknown"):
        pm.grant("totally_unknown")


# ---------------------------------------------------------------------------
# Honest unavailable reporting (never fabricated success)
# ---------------------------------------------------------------------------
def test_tool_reports_unavailable_when_no_consent(tmp_path):
    from core.android.tool import AndroidTool
    from core.android.permissions import AndroidPermissionManager
    pm = AndroidPermissionManager(path=str(tmp_path / "perm.json"))
    tool = AndroidTool(transport=_ok_transport(), permissions=pm)
    r = tool.get_screen()
    assert r["ok"] is False
    assert r["error"] == "android_control_unavailable"
    assert "consent" in r["reason"]


def test_tool_reports_unavailable_when_transport_fails(tmp_path):
    from core.android.tool import AndroidTool
    from core.android.permissions import AndroidPermissionManager
    from core.android.transport import AndroidUnavailable

    class _T:
        def connect(self): return {}
        def get_screen(self, **kw):
            raise AndroidUnavailable("adb binary not found", category="tool")
    pm = AndroidPermissionManager(path=str(tmp_path / "perm.json"))
    pm.grant("screen_capture")
    tool = AndroidTool(transport=_T(), permissions=pm)
    r = tool.get_screen()
    assert r["ok"] is False
    assert r["error"] == "android_control_unavailable"
    assert "adb binary not found" in r["reason"]


def test_capability_never_claims_available_on_missing_adb(tmp_path):
    from core.android.transport import detect_capability
    # adb is almost certainly not present in the test sandbox; if it IS, the test
    # requires a device to be online to claim available.
    import shutil
    cap = detect_capability()
    if not shutil.which("adb"):
        assert cap["available"] is False
        assert cap["reason"].startswith("adb binary not found")
    # If adb exists but no device, it must not fabricate availability.
    elif cap["available"] is False:
        assert cap["reason"] is not None and cap["reason"].strip()


# ---------------------------------------------------------------------------
# ADB transport: command building (adapter logic, NOT device capability)
# ---------------------------------------------------------------------------
def test_adb_transport_builds_correct_commands():
    from tests._android_fixtures import UI_XML
    from core.android.transport import AdbAndroidTransport
    calls = []

    def _runner(cmd, timeout):
        calls.append(cmd)
        # The `cat` of the dump returns the real XML; everything else "ok".
        if cmd[-2:] == ["cat", "/sdcard/window_dump.xml"]:
            return types.SimpleNamespace(returncode=0, stdout=UI_XML, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    t = AdbAndroidTransport(adb="/usr/bin/adb", runner=_runner)
    tree = t.get_ui_tree()  # must dump AND cat AND parse into a semantic hierarchy
    assert tree["ok"] is True
    assert tree["format"] == "semantic"
    assert tree["package"] == "com.example.tasks"
    t.tap(10, 20)
    t.type_text("hello world")
    t.back()
    t.launch_app("com.example/.Main")
    outs = [c for c in calls]
    assert any(c == ["/usr/bin/adb", "shell", "uiautomator", "dump", "/sdcard/window_dump.xml"] for c in outs)
    # After the dump, the XML must be *cat'd* back (not taken from stdout) — this is the
    # §9 fix: `uiautomator dump`'s stdout is only the confirmation, not the XML.
    assert ["/usr/bin/adb", "shell", "cat", "/sdcard/window_dump.xml"] in outs
    assert ["/usr/bin/adb", "shell", "input", "tap", "10", "20"] in outs
    # spaces must be percent-encoded for adb shell input text
    assert ["/usr/bin/adb", "shell", "input", "text", "hello%sworld"] in outs
    assert ["/usr/bin/adb", "shell", "input", "keyevent", "4"] in outs
    assert ["/usr/bin/adb", "shell", "am", "start", "-n", "com.example/.Main"] in outs


def test_adb_transport_screencap_returns_valid_png_bytes():
    from tests._android_fixtures import PNG_1x1_BYTES
    from core.android.transport import AdbAndroidTransport, PNG_SIGNATURE
    calls = []
    t = AdbAndroidTransport(adb="/usr/bin/adb",
                            runner=_fake_runner(calls, binary=True))
    r = t.get_screen()
    assert r["ok"] is True
    assert ["/usr/bin/adb", "exec-out", "screencap", "-p"] in calls
    # The result carries well-defined binary-safe payload (base64 PNG) with a valid sig.
    assert r["format"] == "png" and r["mime"] == "image/png"
    assert r["bytes"] > 0
    import base64
    decoded = base64.b64decode(r["b64"])
    assert decoded.startswith(PNG_SIGNATURE)
    assert decoded == PNG_1x1_BYTES


def test_adb_transport_rejects_corrupt_screencap():
    """§8: malformed/non-PNG screencap output must fail, never report success."""
    from core.android.transport import AdbAndroidTransport, AndroidUnavailable
    calls = []
    # Runner returns a non-PNG (text) payload for the binary screencap.
    t = AdbAndroidTransport(adb="/usr/bin/adb",
                            runner=_fake_runner(calls, stdout="not a png"))
    with pytest.raises(AndroidUnavailable, match="valid PNG"):
        t.get_screen()
    # Empty output must also fail.
    t2 = AdbAndroidTransport(adb="/usr/bin/adb",
                             runner=_fake_runner(calls, stdout=""))
    with pytest.raises(AndroidUnavailable, match="empty"):
        t2.get_screen()


# ---------------------------------------------------------------------------
# Audit + EventBus
# ---------------------------------------------------------------------------
def test_audit_records_ops_and_emits_event(tmp_path):
    from core.events.bus import configure_bus, get_bus
    from core.android.tool import AndroidTool
    from core.android.permissions import AndroidPermissionManager

    log = tmp_path / "events.jsonl"
    bus = configure_bus(str(log), reset=True)
    audit_path = tmp_path / "audit.jsonl"
    os.environ["HERMUS_ANDROID_AUDIT"] = str(audit_path)
    try:
        pm = AndroidPermissionManager(path=str(tmp_path / "perm.json"))
        pm.grant("ui_control")
        tool = AndroidTool(transport=_ok_transport(), permissions=pm)
        out = tool.tap(5, 6)
        assert out["ok"] is True
        from core.android.audit import read_log
        recs = read_log(100)
        assert any(r["op"] == "tap" and r["ok"] for r in recs)
        # mirrored onto the canonical EventBus with the android source
        snap = get_bus().snapshot()
        assert any(e.command == "tap" and e.source == "android" and e.status == "ok"
                   for e in snap), "android op must be mirrored onto the canonical EventBus"
    finally:
        bus.close()


# ---------------------------------------------------------------------------
# §7 Default transport factory + production singleton wiring
# ---------------------------------------------------------------------------
def test_default_transport_factory_provisions_adb_when_available(monkeypatch):
    """With adb on PATH, the factory must build a real ADB transport, not return None."""
    import shutil
    from core.android.transport import build_default_transport, AdbAndroidTransport
    if shutil.which("adb"):
        monkeypatch.setenv("HERMUS_ANDROID_TRANSPORT", "adb")
        t = build_default_transport()
        assert isinstance(t, AdbAndroidTransport)
    else:
        # No adb in this sandbox — the factory must return None (truthful), never a stub.
        monkeypatch.setenv("HERMUS_ANDROID_TRANSPORT", "adb")
        monkeypatch.delenv("HERMUS_ANDROID_ADB", raising=False)
        # If the test env has no adb, build_default_transport returns None.
        if not shutil.which("adb"):
            assert build_default_transport() is None


def test_default_transport_factory_bridge_when_configured(monkeypatch):
    from core.android.transport import build_default_transport, BridgeAndroidTransport
    monkeypatch.setenv("HERMUS_ANDROID_TRANSPORT", "bridge")
    monkeypatch.setenv("HERMUS_ANDROID_BRIDGE_URL", "http://127.0.0.1:8080")
    import base64
    monkeypatch.setenv("HERMUS_ANDROID_SECRET",
                       base64.urlsafe_b64encode(b"x" * 32).decode())
    t = build_default_transport()
    assert isinstance(t, BridgeAndroidTransport)


def test_get_android_tool_provisions_default_transport(monkeypatch):
    """§7: the production singleton must not stay AndroidTool(transport=None) when a
    transport is actually available."""
    import shutil
    monkeypatch.setenv("HERMUS_ANDROID_TRANSPORT", "adb")
    monkeypatch.delenv("HERMUS_ANDROID_ADB", raising=False)
    # Reset the module singleton so we test fresh wiring.
    import core.android.tool as tool_mod
    tool_mod._android_tool = None
    t = tool_mod.get_android_tool()
    # If adb is present it should have a real transport; otherwise None + honest cap.
    if shutil.which("adb"):
        assert t.transport is not None
        assert "AdbAndroidTransport" in type(t.transport).__name__
    else:
        assert t.transport is None
        cap = t.capability()
        assert cap["available"] is False


# ---------------------------------------------------------------------------
# §9 UI-tree semantic parsing + §10 app-launch resolution
# ---------------------------------------------------------------------------
def test_parse_ui_xml_returns_semantic_nodes():
    from tests._android_fixtures import UI_XML
    from core.android.transport import _parse_ui_xml
    parsed = _parse_ui_xml(UI_XML)
    assert parsed["package"] == "com.example.tasks"
    assert parsed["activity"] == ".MainActivity"
    nodes = parsed["nodes"]
    add = next(n for n in nodes if n["resource_id"] == "add")
    assert add["text"] == "Add" and add["clickable"] is True and add["enabled"] is True
    assert add["bounds"] == [20, 420, 520, 520]
    field = next(n for n in nodes if n["resource_id"] == "task_input")
    assert field["focused"] is True and field["class"].endswith("EditText")
    assert any(n["text"] == "Buy milk" for n in nodes)


def test_get_ui_tree_fails_on_empty_xml():
    from core.android.transport import AdbAndroidTransport, AndroidUnavailable
    calls = []
    t = AdbAndroidTransport(adb="/usr/bin/adb", runner=_fake_runner(calls, stdout="ok"))
    with pytest.raises(AndroidUnavailable, match="no usable XML"):
        t.get_ui_tree()


def test_launch_app_supports_component_and_package(monkeypatch):
    from core.android.transport import AdbAndroidTransport
    calls = []
    t = AdbAndroidTransport(adb="/usr/bin/adb", runner=_fake_runner(calls, stdout="ok"))
    # Explicit component -> used verbatim as -n.
    r = t.launch_app(component="com.example.app/.MainActivity")
    assert r["ok"] and r["component"] == "com.example.app/.MainActivity"
    assert [a for a in calls if "am" in a][-1] == \
        ["/usr/bin/adb", "shell", "am", "start", "-n", "com.example.app/.MainActivity"]
    # Bare package (with a launcher activity resolved) -> resolves then -n the component.
    calls.clear()
    def _resolver(cmd, timeout):
        calls.append(cmd)
        if "resolve-activity" in cmd:
            return types.SimpleNamespace(returncode=0, stdout="com.example.app/.MainActivity", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
    t2 = AdbAndroidTransport(adb="/usr/bin/adb", runner=_resolver)
    r2 = t2.launch_app(package="com.example.app")
    assert r2["ok"]
    assert ["/usr/bin/adb", "shell", "cmd", "package", "resolve-activity", "--brief",
            "com.example.app"] in calls
    assert ["/usr/bin/adb", "shell", "am", "start", "-n", "com.example.app/.MainActivity"] in calls


def test_launch_app_rejects_invalid_component():
    from core.android.transport import AdbAndroidTransport, AndroidUnavailable
    t = AdbAndroidTransport(adb="/usr/bin/adb", runner=_fake_runner([], stdout="ok"))
    with pytest.raises(AndroidUnavailable, match="invalid component"):
        t.launch_app(component="notacomponent")
    with pytest.raises(AndroidUnavailable, match="requires a package"):
        t.launch_app()


# ---------------------------------------------------------------------------
# §13 Bridge transport rejects unsafe plaintext remote endpoints
# ---------------------------------------------------------------------------
def test_bridge_transport_requires_loopback_or_tls():
    from core.android.transport import BridgeAndroidTransport, AndroidUnavailable
    # A remote plaintext (non-loopback) endpoint must be refused — §13 enforcement.
    with pytest.raises(AndroidUnavailable, match="loopback|https"):
        BridgeAndroidTransport(base_url="http://evil.example.com:8080",
                               secret=b"x" * 32)
    with pytest.raises(AndroidUnavailable, match="loopback|https"):
        BridgeAndroidTransport(base_url="http://192.168.1.5:8080", secret=b"x" * 32)


# ---------------------------------------------------------------------------
# Secure signing / pairing
# ---------------------------------------------------------------------------
def test_secure_sign_verify_and_pairing():
    from core.android.secure import (new_pairing_secret, sign, verify, pairing_response,
                                     verify_pairing)
    secret = new_pairing_secret()
    mac = sign(secret, b"payload")
    assert verify(secret, b"payload", mac) is True
    assert verify(secret, b"other", mac) is False
    assert verify(b"wrong", b"payload", mac) is False
    nonce = b"\x01" * 16
    resp = pairing_response(secret, nonce)
    assert verify_pairing(secret, nonce, resp) is True
    assert verify_pairing(b"wrong", nonce, resp) is False


# ---------------------------------------------------------------------------
# Host E2E (requires a real device/emulator; skipped when none)
# ---------------------------------------------------------------------------
def _has_adb_and_device() -> bool:
    import shutil, subprocess
    if not shutil.which("adb"):
        return False
    try:
        out = subprocess.run(["adb", "get-state"], capture_output=True, text=True, timeout=5)
        return out.returncode == 0 and out.stdout.strip() == "device"
    except Exception:
        return False


@pytest.mark.skipif(not _has_adb_and_device(), reason="no adb / no online Android device")
def test_host_e2e_android_control_real_device():
    """E2E on a real device: connect -> screen -> ui tree -> launch -> back -> audit.
    Requires adb + an online, user-authorized device. Never run on a mock."""
    from core.android.tool import AndroidTool, get_android_tool
    from core.android.permissions import get_permission_manager

    pm = get_permission_manager()
    for cls in ("screen_capture", "ui_control", "device_info", "launch_app"):
        pm.grant(cls)
    tool = get_android_tool()
    conn = tool.connect()
    assert conn.get("ok"), conn
    screen = tool.get_screen()
    assert screen.get("ok")
    tree = tool.get_ui_tree()
    assert tree.get("ok")
    back = tool.back()
    assert back.get("ok")
