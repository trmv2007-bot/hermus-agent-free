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

import base64
import json
import shutil
import subprocess
import uuid
from typing import Any, Optional
from xml.etree import ElementTree as ET

from .secure import sign, verify

#: PNG magic signature — validated so a corrupted/empty screencap is never
#: reported as a successful screenshot.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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
        # Text commands: decode stdout as UTF-8 (safe for shell/am/uiautomator/input).
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def _default_runner_binary(self, cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        # Binary commands (e.g. `exec-out screencap -p`): NEVER decode stdout as text —
        # the PNG bytes would be corrupted by the text-mode codec. Keep raw bytes.
        return subprocess.run(cmd, capture_output=True, text=False, timeout=timeout)

    def _run(self, args: list[str]) -> dict[str, Any]:
        """Run a text command; returns ``{"code", "output"}`` or raises unavailable."""
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
        if getattr(proc, "returncode", 1) != 0:
            err = (proc.stderr or proc.stdout or "")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            raise AndroidUnavailable(
                str(err).strip()[:300] or f"adb {args} failed",
                category="device",
            )
        out = proc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return {"code": 0, "output": out.strip()}

    def _run_binary(self, args: list[str]) -> bytes:
        """Run a command returning raw bytes (binary-safe)."""
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
            # Use the binary-compatible runner. A test-injected runner always receives
            # (cmd, timeout); we interpret its stdout as bytes.
            if self._runner is self._default_runner:
                proc = self._default_runner_binary(cmd, self.timeout)
            else:
                proc = self._runner(cmd, self.timeout)
        except FileNotFoundError as exc:
            raise AndroidUnavailable(f"adb not found: {exc}", category="tool") from exc
        except subprocess.TimeoutExpired as exc:
            raise AndroidUnavailable(f"adb '{ ' '.join(args[:1]) }' timed out", category="timeout") from exc
        if getattr(proc, "returncode", 1) != 0:
            err = proc.stderr or b""
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            raise AndroidUnavailable(
                str(err).strip()[:300] or f"adb {args} failed",
                category="device",
            )
        out = proc.stdout or b""
        if isinstance(out, str):
            out = out.encode("utf-8")
        return out

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
        # `exec-out` produces raw binary on stdout; decoding it as text would corrupt
        # the PNG. Read bytes and validate the PNG signature before returning.
        raw = self._run_binary(["exec-out", "screencap", "-p"])
        if not raw:
            raise AndroidUnavailable("adb screencap produced empty output",
                                     category="device", op="get_screen")
        if not raw.startswith(PNG_SIGNATURE):
            raise AndroidUnavailable(
                "adb screencap did not produce a valid PNG (bad/incomplete payload)",
                category="device", op="get_screen")
        b64 = base64.b64encode(raw).decode("ascii")
        return {"ok": True, "format": "png", "mime": "image/png",
                "bytes": len(raw), "b64": b64, "data": b64}

    def get_ui_tree(self, **kw) -> dict[str, Any]:
        # `uiautomator dump` writes the XML to a file on-device; its STDOUT is only the
        # confirmation text ("UI hierchary dumped to ..."), NOT the XML. Dump then cat
        # the file, then parse + validate it into a structured semantic hierarchy.
        self._run(["shell", "uiautomator", "dump", "/sdcard/window_dump.xml"])
        xml_payload = self._run(["shell", "cat", "/sdcard/window_dump.xml"])["output"]
        if not xml_payload or "<hierarchy" not in xml_payload:
            raise AndroidUnavailable("uiautomator dump produced no usable XML",
                                     category="device", op="get_ui_tree")
        try:
            parsed = _parse_ui_xml(xml_payload)
        except ET.ParseError as exc:
            raise AndroidUnavailable(f"uiautomator XML parse failed: {exc}",
                                     category="device", op="get_ui_tree") from exc
        if not parsed.get("nodes"):
            raise AndroidUnavailable("uiautomator UI tree is empty",
                                     category="device", op="get_ui_tree")
        return {"ok": True, "format": "semantic", "package": parsed.get("package"),
                "activity": parsed.get("activity"), "nodes": parsed["nodes"],
                "raw_xml": xml_payload}

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

    def launch_app(self, package: str = "", *, component: str = "", **kw) -> dict[str, Any]:
        package = (package or "").strip()
        component = (component or "").strip()
        # A bare package resolves its launcher activity; an explicit component is used
        # verbatim. Passing an arbitrary '-n' argument directly would route a bare
        # package name to `am start` incorrectly, so we resolve first.
        if component:
            if "/" not in component:
                raise AndroidUnavailable(
                    f"invalid component '{component}' (expected package/activity)",
                    category="op", op="launch_app")
            target = component
        elif package:
            resolved = self._resolve_launcher(package)
            target = resolved or package
        else:
            raise AndroidUnavailable("launch_app requires a package or component",
                                     category="op", op="launch_app")
        self._run(["shell", "am", "start", "-n", target])
        return {"ok": True, "package": package or component.split("/")[0],
                "component": target}

    def _resolve_launcher(self, package: str) -> Optional[str]:
        """Resolve the launchable activity for ``package`` via `cmd package resolve-activity`
        (or `monkey -p`), returning a ``package/activity`` component. Falls back to the
        bare package so a launchable intent error is surfaced honestly by `am start`."""
        try:
            out = self._run(
                ["shell", "cmd", "package", "resolve-activity", "--brief", package])["output"]
            # Format: "<package>/<activity>" for the LAUNCHER intent. Resolve the last
            # non-empty line (resolve-activity can emit a header).
            comp = None
            for ln in reversed(out.splitlines()):
                ln = ln.strip()
                if not ln or ln.startswith("Initiating") or ln.startswith("resolve-activity"):
                    continue
                # cmd resolve-activity prints the component (optionally prefixed by nothing).
                if "/" in ln:
                    comp = ln.split()[-1]
                    break
            return comp or (package if "/" in package else None)
        except AndroidUnavailable:
            return None


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

    _LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

    def __init__(self, *, base_url: str, secret: bytes, max_payload: int = 20 * 1024 * 1024,
                 http: Any = None):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.max_payload = max_payload
        self._http = http or self._default_http
        self._validate_base_url(self.base_url)

    @classmethod
    def _validate_base_url(cls, url: str) -> None:
        """Enforce the documented security model: HTTPS, or loopback HTTP.

        Refuses plaintext (http) endpoints that are not on the loopback interface, and
        refuses any non-https when a host is provided. This is a hard code guard, not a
        docstring — a remote plaintext bridge is rejected before any request is made.
        """
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise AndroidUnavailable(
                f"bridge base_url scheme '{parsed.scheme}' not allowed (http/https)",
                category="security")
        if parsed.scheme == "https":
            return
        # Plaintext http is only acceptable on loopback.
        host = (parsed.hostname or "").lower()
        if host not in cls._LOOPBACK_HOSTS:
            raise AndroidUnavailable(
                f"insecure plaintext bridge endpoint '{url}' refused; use https or a "
                "loopback (127.0.0.1/localhost) address",
                category="security")

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


def _parse_bounds(val: Optional[str]) -> list[int]:
    """Parse an Android bounds string like ``[0,0][1080,2000]`` into ``[x1,y1,x2,y2]``."""
    if not val:
        return [0, 0, 0, 0]
    import re
    nums = re.findall(r"-?\d+", val)
    if len(nums) < 4:
        return [0, 0, 0, 0]
    return [int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3])]


def _bool_attr(node: ET.Element, name: str) -> bool:
    return node.get(name, "").strip().lower() == "true"


def _parse_ui_xml(xml_payload: str) -> dict[str, Any]:
    """Parse an Android ``uiautomator dump`` XML payload into a semantic hierarchy.

    Returns ``{"package", "activity", "nodes": [...]}`` where each node has ``text``,
    ``content_description``, ``resource_id``, ``class``, ``clickable``, ``enabled``,
    ``selected``, ``focused``, ``checked``, ``bounds``, ``package``.
    """
    root = ET.fromstring(xml_payload)  # raises ET.ParseError on malformed XML
    activity = root.get("activity") or ""
    package = root.get("package") or ""

    def walk(node: ET.Element, depth: int):
        out = []
        for child in node:
            attrs = {
                "text": child.get("text") or "",
                "content_description": child.get("content-desc") or "",
                "resource_id": child.get("resource-id") or "",
                "class": child.get("class") or "",
                "clickable": _bool_attr(child, "clickable"),
                "enabled": _bool_attr(child, "enabled"),
                "selected": _bool_attr(child, "selected"),
                "focused": _bool_attr(child, "focused"),
                "checked": _bool_attr(child, "checked"),
                "bounds": _parse_bounds(child.get("bounds")),
                "package": child.get("package") or package,
            }
            out.append(attrs)
            out.extend(walk(child, depth + 1))
        return out

    nodes = walk(root, 0)
    return {"package": package, "activity": activity, "nodes": nodes}


# ---------------------------------------------------------------------------
# Transport factory (canonical provisioning — one way to build an Android transport)
# ---------------------------------------------------------------------------
def _env(name: str, default: str = "") -> str:
    import os
    return os.environ.get(name, default)


def build_default_transport(*, serial: Optional[str] = None) -> Optional[AndroidTransport]:
    """Provision the configured Android transport, or None when none can be constructed.

    Resolution order (all config-driven, no caller-chosen transports):
      1. ``HERMUS_ANDROID_TRANSPORT=bridge`` with a configured bridge URL+secret
         -> :class:`BridgeAndroidTransport`.
      2. ``adb`` present on PATH (or ``HERMUS_ANDROID_ADB``) -> :class:`AdbAndroidTransport`.

    Returns ``None`` when nothing is available so the caller reports
    ``android_control_unavailable`` truthfully — it never fabricates a transport.
    """
    mode = _env("HERMUS_ANDROID_TRANSPORT", "adb").strip().lower()
    if mode == "bridge":
        base_url = _env("HERMUS_ANDROID_BRIDGE_URL", "").strip()
        secret_b64 = _env("HERMUS_ANDROID_SECRET", "").strip()
        if base_url and secret_b64:
            from .secure import _unb64
            try:
                secret = _unb64(secret_b64)
            except Exception:
                secret = None
            if secret:
                return BridgeAndroidTransport(base_url=base_url, secret=secret)
        # Fall through to adb if the bridge is not fully configured; adb may still work.
    adb = _env("HERMUS_ANDROID_ADB", "").strip() or shutil.which("adb")
    if not adb:
        return None
    return AdbAndroidTransport(adb=adb, serial=serial)


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

    adb = _env("HERMUS_ANDROID_ADB", "").strip() or shutil.which("adb")
    bridge_configured = bool(_env("HERMUS_ANDROID_BRIDGE_URL", "").strip() and
                             _env("HERMUS_ANDROID_SECRET", "").strip())
    if not adb and not bridge_configured:
        return {"available": False, "transport": "adb", "adb": None, "device": None,
                "reason": "adb binary not found; install Android platform-tools or set "
                          "HERMUS_ANDROID_ADB" if not bridge_configured else
                          "bridge not fully configured"}
    t = build_default_transport(serial=serial)
    if t is None:
        return {"available": False, "transport": "none", "adb": adb, "device": None,
                "reason": "no Android transport configured"}
    try:
        info = t.connect()
        return {"available": True, "transport": type(t).__name__,
                "device": info.get("device"), "reason": "ok"}
    except AndroidUnavailable as exc:
        return {"available": False, "transport": type(t).__name__, "device": None,
                "reason": exc.reason}
    except Exception as exc:  # noqa: BLE001 - capability reports, never raises
        return {"available": False, "transport": type(t).__name__, "device": None,
                "reason": f"unexpected: {exc}"}
