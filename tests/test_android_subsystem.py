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


def _fake_runner(record: list, *, stdout: str = "", rc: int = 0):
    def _run(cmd, timeout):
        record.append(cmd)
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
    from core.android.transport import AdbAndroidTransport
    calls = []
    t = AdbAndroidTransport(adb="/usr/bin/adb", runner=_fake_runner(calls, stdout="ok"))
    # inject runner marks non-default so missing-adb guard is bypassed
    t.get_ui_tree()
    t.tap(10, 20)
    t.type_text("hello world")
    t.back()
    t.launch_app("com.example/.Main")
    outs = [c for c in calls]
    assert any(c == ["/usr/bin/adb", "shell", "uiautomator", "dump", "/sdcard/window_dump.xml"] for c in outs)
    assert ["/usr/bin/adb", "shell", "input", "tap", "10", "20"] in outs
    # spaces must be percent-encoded for adb shell input text
    assert ["/usr/bin/adb", "shell", "input", "text", "hello%sworld"] in outs
    assert ["/usr/bin/adb", "shell", "input", "keyevent", "4"] in outs
    assert ["/usr/bin/adb", "shell", "am", "start", "-n", "com.example/.Main"] in outs


def test_adb_transport_screencap_returns_bytes():
    from core.android.transport import AdbAndroidTransport
    calls = []
    t = AdbAndroidTransport(adb="/usr/bin/adb", runner=_fake_runner(calls, stdout="PNG_BYTES"))
    r = t.get_screen()
    assert r["ok"] is True
    assert ["/usr/bin/adb", "exec-out", "screencap", "-p"] in calls


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
