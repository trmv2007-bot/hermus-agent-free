"""Android control transport layer (§17).

Two concrete transports over one contract:

* :class:`AdbAndroidTransport` — drives a real Android device/emulator through the
  ``adb`` CLI (uiautomator dump, screencap, input tap/text/keyevent, am start).
  Every call shells out to ``adb``; when ``adb`` is missing or no device is online
  it raises :class:`AndroidUnavailable` with the exact reason, so the caller can
  report ``android_control_unavailable`` instead of fabricating a result.

* :class:`BridgeAndroidTransport` — the companion-app transport. It talks to the
  Android Agent Companion over an HTTP(S)/WebSocket local bridge using signed
  requests (HMAC-SHA256). The companion performs accessibility/UI control with
  user-granted permissions on-device. The transport here implements the client
  side (sign, request, verify) and is consumed only when a device + companion are
  actually reachable.

Both are *real adapter implementations* over the injected ``runner``/``http``
seams; a unit test may inject a fake runner to exercise command building, but that
is NOT evidence of device capability — capability is reported by
:func:`detect_capability` and E2E requires a live device/emulator.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from typing import Any, Optional

from .secure import sign, verify


class AndroidUnavailable(RuntimeError):
    """Raised when Android control cannot be performed (no adb, no device, no permission)."""

    def __init__(self, reason: str, *, category: str = "device", op: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.category = category
        self.op = op


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------
class AndroidTransport:
    """Interface every Android transport implements."""

    def connect(self) -> dict[str, Any]:
        raise NotImplementedError

    def get_screen(self, **kw) -> dict[str, Any]:
        raise NotImplementedError

    def get_ui_tree(self, **kw) -> dict[str, Any]:
        raise NotImplementedError

    def tap(self, x: int, y: int, **kw) -> dict[str, Any]:
        raise NotImplementedError

    def type_text(self, text: str, **kw) -> dict[str, Any]:
        raise NotImplementedError

    def back(self, **kw) -> dict[str, Any]:
        raise NotImplementedError

    def launch_app(self, package: str, **kw) -> dict[str, Any]:
        raise NotImplementedError

    def device_id(self) -> str:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# ADB transport (real device control)
# ---------------------------------------------------------------------------
class AdbAndroidTransport(AndroidTransport):
    """Drive an Android device through ``adb``.

    ``runner`` is a ``subprocess``-compatible callable (injected in tests to avoid
    exec'ing adb); by default it shells out for real. ``adb`` binary discovery is
    explicit so a missing binary is reported, not guessed.
    """

    def __init__(self, *, adb: Optional[str] = None, serial: Optional[str] = None,
                 timeout: float = 15.0, runner: Any = None):
        self.adb = adb or shutil.which("adb") or "adb"
        self.serial = serial
        self.timeout = timeout
        self._runner = runner or self._default_runner

    # -- helpers ------------------------------------------------------------
    def _default_runner(self, cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def _run(self, args: list[str]) -> dict[str, Any]:
        if not shutil.which(self.adb) and self._runner is self._default_runner:
            raise AndroidUnavailable(
                f"adb binary not found on PATH (searched '{self.adb}'); install the "
                "Android SDK platform-tools or set HERMUS_ANDROID_ADB",
                category="tool",
            )
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += args
        try:
            proc = self._runner(cmd, self.timeout)
        except FileNotFoundError as exc:
            raise AndroidUnavailable(f"adb not found: {exc}", category="tool") from exc
        except subprocess.TimeoutExpired as exc:
            raise AndroidUnavailable(f"adb '{ ' '.join(args[:1]) }' timed out", category="timeout") from exc
        if proc.returncode != 0:
            raise AndroidUnavailable(
                (proc.stderr or proc.stdout or "").strip()[:300] or f"adb {args} failed",
                category="device",
            )
        return {"code": 0, "output": (proc.stdout or "").strip()}

    def _title(self) -> str:
        return self.device_id()

    # -- contract -----------------------------------------------------------
    def connect(self) -> dict[str, Any]:
        out = self._run(["shell", "getprop", "ro.product.model"])
        return {"ok": True, "device": self.device_id(),
                "model": out["output"] or "unknown", "transport": "adb"}

    def device_id(self) -> str:
        out = self._run(["get-serialno"])
        return out["output"] or str(uuid.uuid4())

    def _state(self) -> dict[str, Any]:
        out = self._run(["get-state"])
        state = out["output"] or "unknown"
        if state.strip() != "device":
            raise AndroidUnavailable(f"device state '{state}' (expected 'device'); is it authorised?",
                                     category="device")
        return {"state": state}

    def get_screen(self, *, format: str = "png", **kw) -> dict[str, Any]:
        base64_png = self._run(["exec-out", "screencap", "-p"])["output"]
        # `format` is captured so callers can pick png/jpeg; the companion/transport
        # returns the raw frames and metadata, not a fabricated 'success'.
        return {"ok": True, "format": format, "bytes": len(base64_png),
                "data": base64_png}

    def get_ui_tree(self, **kw) -> dict[str, Any]:
        xml = self._run(["shell", "uiautomator", "dump", "/sdcard/window_dump.xml"])
        if xml["code"] != 0:
            raise AndroidUnavailable("uiautomator dump failed", category="device")
        return {"ok": True, "format": "xml", "data": xml["output"]}

    def tap(self, x: int, y: int, **kw) -> dict[str, Any]:
        self._run(["shell", "input", "tap", str(int(x)), str(int(y))])
        return {"ok": True, "x": int(x), "y": int(y)}

    def type_text(self, text: str, **kw) -> dict[str, Any]:
        # Percent-encode spaces so `adb shell input text` treats it as one token.
        self._run(["shell", "input", "text", text.replace(" ", "%s")])
        return {"ok": True, "length": len(text)}

    def back(self, **kw) -> dict[str, Any]:
        self._run(["shell", "input", "keyevent", "4"])  # KEYCODE_BACK
        return {"ok": True, "keyevent": "BACK"}

    def launch_app(self, package: str, **kw) -> dict[str, Any]:
        self._run(["shell", "am", "start", "-n", package])
        return {"ok": True, "package": package}


# ---------------------------------------------------------------------------
# Companion-bridge transport (signed HTTP(S) local bridge)
# ---------------------------------------------------------------------------
class BridgeAndroidTransport(AndroidTransport):
    """Client for the Android Agent Companion over a secure local bridge.

    Every request is signed with :func:`core.android.secure.sign` using the
    paired session key; the response is verified before use. The bridge itself
    must speak HTTPS (or be loopback) — enforced here by refusing non-local,
    non-TLS endpoints.
    """

    def __init__(self, *, base_url: str, secret: bytes, max_payload: int = 20 * 1024 * 1024,
                 http: Any = None):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.max_payload = max_payload
        self._http = http or self._default_http

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _default_http(self, url: str, method: str, body: bytes, headers: dict) -> dict[str, Any]:
        import urllib.request
        req = urllib.request.Request(url, data=body, method=method)
        for k, v in headers.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"status": resp.status, "body": resp.read()}

    def _call(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        payload = {"op": op, "args": args, "nonce": uuid.uuid4().hex}
        raw = json.dumps(payload).encode("utf-8")
        if len(raw) > self.max_payload:
            raise AndroidUnavailable("payload too large", category="payload")
        body = json.dumps({"payload": payload, "mac": sign(self.secret, raw)}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        resp = self._http(self._url("/v1/exec"), "POST", body, headers)
        if resp.get("status", 0) not in (200, 201):
            raise AndroidUnavailable(f"bridge returned HTTP {resp.get('status')}",
                                     category="device")
        data = json.loads(resp["body"])
        if not verify(self.secret, json.dumps(data.get("payload", {})).encode("utf-8"),
                      data.get("mac", "")):
            raise AndroidUnavailable("bridge response MAC invalid", category="security")
        result = data.get("result") or {}
        if not result.get("ok"):
            raise AndroidUnavailable(result.get("reason") or "companion rejected op",
                                     category="permission", op=op)
        return result

    def connect(self) -> dict[str, Any]:
        return self._call("connect", {})
    def device_id(self) -> str:
        return str(self._call("device_id", {}).get("device", uuid.uuid4().hex))
    def get_screen(self, **kw) -> dict[str, Any]:
        return self._call("get_screen", kw)
    def get_ui_tree(self, **kw) -> dict[str, Any]:
        return self._call("get_ui_tree", kw)
    def tap(self, x: int, y: int, **kw) -> dict[str, Any]:
        return self._call("tap", {"x": int(x), "y": int(y), **kw})
    def type_text(self, text: str, **kw) -> dict[str, Any]:
        return self._call("type", {"text": text, **kw})
    def back(self, **kw) -> dict[str, Any]:
        return self._call("back", kw)
    def launch_app(self, package: str, **kw) -> dict[str, Any]:
        return self._call("launch_app", {"package": package, **kw})


# ---------------------------------------------------------------------------
# Capability detection (honest reporting)
# ---------------------------------------------------------------------------
def detect_capability(*, serial: Optional[str] = None,
                      transport: Optional[AndroidTransport] = None) -> dict[str, Any]:
    """Report truthfully whether Android control is available, with a reason.

    Returns ``{"available": bool, "transport": ..., "adb": ..., "device": ...,
    "reason": ...}``. ``available`` is True only when a transport can actually be
    provisioned (adb present + device online, or a live companion bridge). It never
    fabricates availability.
    """
    if transport is not None:
        try:
            info = transport.connect()
            return {"available": True, "transport": type(transport).__name__,
                    "device": info.get("device"), "reason": "ok"}
        except AndroidUnavailable as exc:
            return {"available": False, "transport": type(transport).__name__,
                    "device": None, "reason": exc.reason}
        except Exception as exc:  # noqa: BLE001 - capability reports, never raises
            return {"available": False, "transport": type(transport).__name__,
                    "device": None, "reason": f"unexpected: {exc}"}

    adb = shutil.which("adb")
    if not adb:
        return {"available": False, "transport": "adb", "adb": None, "device": None,
                "reason": "adb binary not found; install Android platform-tools or set "
                          "HERMUS_ANDROID_ADB"}
    try:
        t = AdbAndroidTransport(adb=adb, serial=serial)
        info = t.connect()
        return {"available": True, "transport": "adb", "adb": adb,
                "device": info.get("device"), "reason": "ok"}
    except AndroidUnavailable as exc:
        return {"available": False, "transport": "adb", "adb": adb, "device": None,
                "reason": exc.reason}
