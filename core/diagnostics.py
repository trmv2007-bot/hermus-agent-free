"""Hermus install / health diagnostics (Phase D).

``hermus doctor`` owns the deterministic capability probes used by both the
CLI and :mod:`bootstrap`.  The fast mode is cheap and side-effect-light.  The
installer asks for ``deep=True`` so the same Doctor boundary additionally
exercises the real Scrapling, browser, gateway and storage paths instead of
mistaking an import or an on-disk binary for an operational subsystem.

The deep probes deliberately use a temporary loopback page and never call an
LLM, write credentials, or alter user projects/memory.  A browser is closed in
all paths and the temporary server is always shut down.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

# Installer/Doctor status vocabulary.  Keep these exact strings stable: the
# setup report is consumed by humans and by automation.
INSTALLED_VERIFIED = "✅ INSTALLED + VERIFIED"
INSTALLED_UNVERIFIED = "⚠️ INSTALLED BUT NOT VERIFIED"
MISSING_BROKEN = "❌ MISSING/BROKEN"
OPTIONAL_UNAVAILABLE = "⏭️ OPTIONAL/UNAVAILABLE ON THIS PLATFORM"

REQUIRED_PACKAGES = [
    ("pydantic", "core configuration"),
    ("dotenv", "python-dotenv / configuration loading"),
    ("requests", "LLM provider + HTTP calls"),
    ("tiktoken", "token accounting"),
    ("PIL", "image handling / screen capture"),
    ("fastapi", "gateway / dashboard"),
    ("uvicorn", "gateway server"),
    ("multipart", "multipart uploads"),
    ("psutil", "resource telemetry"),
    ("httpx", "gateway TestClient / HTTP verification"),
    ("feedparser", "RSS/web feed tools"),
    ("pypdf", "document extraction"),
    ("yaml", "configuration and skill files"),
    ("imageio_ffmpeg", "media/recording runtime"),
    ("apscheduler", "scheduled jobs"),
    ("prompt_toolkit", "terminal interface"),
    ("rich", "terminal reporting"),
    ("websockets", "gateway streaming"),
]

RECOMMENDED_PACKAGES = [
    ("websockets", "live dashboard WebSocket"),
    ("ddgs", "free web search"),
    ("playwright", "headless browser"),
    ("scrapling", "canonical web acquisition"),
    ("faster_whisper", "local voice transcription"),
    ("pyautogui", "real desktop mouse/keyboard control"),
    ("pygetwindow", "window management"),
]


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _import_status(name: str) -> tuple[bool, str]:
    """Import a module and retain the exact exception without leaking secrets."""
    try:
        importlib.import_module(name)
        return True, "import OK"
    except Exception as exc:  # noqa: BLE001 - a report must contain every failure
        return False, f"{type(exc).__name__}: {exc}"[:400]


def _check(name: str, ok: bool, detail: str, hint: str = "", level: str = "required") -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail, "hint": hint, "level": level}


def _ffmpeg_available() -> bool:
    try:
        from core.computer import VideoWriter

        return bool(VideoWriter.available().get("available"))
    except Exception:  # noqa: BLE001
        return False


def run_diagnostics(*, deep: bool = False) -> dict[str, Any]:
    """Run the existing Doctor checks.

    ``deep=False`` preserves the inexpensive ``hermus doctor`` behavior.  The
    installer uses ``deep=True`` to add operational checks and to produce the
    subsystem report consumed by the final setup summary.
    """
    checks: list[dict[str, Any]] = []

    # Python version
    py_ok = sys.version_info >= (3, 10)
    checks.append(_check(
        "python",
        py_ok,
        f"Python {platform.python_version()} ({platform.system()})",
        "Python 3.10+ is required.",
    ))

    # Required packages: import, not just find_spec, so broken native imports
    # are surfaced to the operator.
    for pkg, role in REQUIRED_PACKAGES:
        ok, detail = _import_status(pkg)
        checks.append(_check(pkg, ok, detail, f"pip install {pkg}  ({role})"))

    # Recommended packages
    for pkg, role in RECOMMENDED_PACKAGES:
        ok, detail = _import_status(pkg)
        checks.append(_check(pkg, ok, detail, f"pip install {pkg}  ({role})", level="recommended"))

    # FFmpeg
    ff = _ffmpeg_available()
    checks.append(_check("ffmpeg", ff, "video encoding available" if ff else "not detected",
                         "pip install imageio-ffmpeg (or provide ffmpeg) for MP4/WebM recording", level="recommended"))

    # Computer control backends
    pyautogui = _importable("pyautogui")
    pygetwindow = _importable("pygetwindow")
    control_ok = pyautogui and pygetwindow
    checks.append(_check(
        "desktop_control",
        control_ok,
        f"pyautogui={pyautogui}, pygetwindow={pygetwindow}",
        "pip install pyautogui pygetwindow for real pointer/keyboard control (dry-run otherwise).",
        level="recommended",
    ))

    # Data directory writability
    data_root = REPO_ROOT / "data"
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        probe = data_root / ".doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks.append(_check("data_dir", True, str(data_root), "ensure data/ is writable"))
    except OSError as exc:
        checks.append(_check("data_dir", False, str(data_root), f"data/ not writable: {exc}"))

    # Gateway token (recommended when exposing the control room / remote control)
    token = os.getenv("HERMUS_GATEWAY_TOKEN") or os.getenv("HERMUS_TOKEN")
    checks.append(_check(
        "gateway_auth",
        bool(token),
        "gateway token configured" if token else "no gateway token (open local access)",
        "Set HERMUS_GATEWAY_TOKEN before exposing /control (or /remote, /computer APIs) beyond localhost.",
        level="recommended",
    ))

    # Computer agent import (core system)
    try:
        import core.computer  # noqa: F401

        checks.append(_check("computer_agent", True, "core.computer imports OK", ""))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("computer_agent", False, f"import error: {exc}",
                             "Run: pip install -r requirements.txt"))

    checks.extend(_web_acquisition_checks())

    subsystems: dict[str, dict[str, Any]] = {}
    if deep:
        subsystems = _deep_subsystems()
        for item in subsystems.values():
            checks.append({
                "name": item["name"],
                "ok": item["ok"],
                "detail": item["detail"],
                "hint": item.get("hint", ""),
                "level": "required" if item.get("required") else "recommended",
                "status": item["status"],
            })
        # This is the Doctor boundary itself; no second installer-specific
        # capability implementation is allowed to claim a different result.
        subsystems["doctor"] = _subsystem(
            "Doctor",
            INSTALLED_VERIFIED,
            "core.diagnostics fast and deep capability probes completed",
            required=True,
        )

    required_ok = all(c["ok"] for c in checks if c["level"] == "required")
    recommended_ok = all(c["ok"] for c in checks if c["level"] == "recommended")
    if (os.environ.get("TERMUX_VERSION") or "android" in platform.system().lower()
            or "android" in platform.platform().lower()):
        platform_family = "android-termux"
    elif platform.system() == "Linux":
        platform_family = "linux"
    elif platform.system() == "Darwin":
        platform_family = "macos"
    else:
        platform_family = platform.system().lower() or "unknown"
    return {
        "generated": _now(),
        "platform": platform.platform(),
        "platform_family": platform_family,
        "hostname": platform.node(),
        "overall": {
            "ok": required_ok,
            "required": required_ok,
            "recommended": recommended_ok,
            "passed": sum(1 for c in checks if c["ok"]),
            "total": len(checks),
        },
        "checks": checks,
        "subsystems": subsystems,
    }


def _now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


def _web_acquisition_checks() -> list[dict[str, Any]]:
    """Honest web-subsystem capability checks (spec §21).

    Never reports a capability as working merely because an import succeeds:
    each status comes from core.web.capabilities, which distinguishes
    available / unavailable / not_installed / not_verified. All checks are
    "recommended" in fast Doctor mode — deep mode applies the configured web
    profile to the operational Scrapling/Chromium checks.
    """
    from .web_status import web_status_checks

    return web_status_checks()


def _subsystem(
    name: str,
    status: str,
    detail: str,
    *,
    required: bool = False,
    hint: str = "",
    evidence: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "ok": status == INSTALLED_VERIFIED,
        "required": bool(required),
        "detail": str(detail),
        "hint": str(hint),
        "evidence": evidence or {},
    }


def _command_check(command: str, *args: str) -> tuple[bool, str]:
    """Run a bounded version/status command without shell interpolation."""
    path = shutil.which(command)
    if not path:
        return False, f"{command} executable not found"
    try:
        result = subprocess.run(
            [path, *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{command}: {type(exc).__name__}: {exc}"
    output = (result.stdout or result.stderr or "").strip().splitlines()
    version = output[0][:240] if output else f"exit={result.returncode}"
    if result.returncode != 0:
        return False, f"{version} (exit {result.returncode})"
    return True, version


def _interpreter_is_project_venv() -> bool:
    """Whether this process is using this checkout's .venv."""
    venv_dir = (REPO_ROOT / ".venv").resolve()
    try:
        return Path(sys.prefix).resolve() == venv_dir and sys.prefix != sys.base_prefix
    except OSError:
        return False


def _dependency_runtime_check() -> tuple[bool, str, dict[str, Any]]:
    """Import the canonical required modules and run ``pip check``."""
    try:
        from bootstrap import REQUIRED_IMPORTS
    except Exception as exc:  # pragma: no cover - only a damaged checkout
        return False, f"cannot read canonical dependency manifest: {exc}", {}

    failures: dict[str, str] = {}
    for module in REQUIRED_IMPORTS:
        ok, detail = _import_status(module)
        if not ok:
            failures[module] = detail

    pip_ok, pip_detail = _command_check(sys.executable, "-m", "pip", "check")
    # _command_check uses shutil.which(command); an absolute interpreter is a
    # valid command, but shutil.which can return it only when executable. Keep a
    # direct fallback for unusual Windows/Termux paths.
    if not pip_ok and Path(sys.executable).exists():
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "check"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            pip_ok = result.returncode == 0
            pip_detail = (result.stdout or result.stderr or "pip check failed").strip()[-500:]
        except (OSError, subprocess.SubprocessError) as exc:
            pip_detail = f"pip check: {type(exc).__name__}: {exc}"
    if failures:
        return False, f"imports failed: {failures}; pip={pip_detail}", {"imports": failures, "pip": pip_detail}
    if not pip_ok:
        return False, f"pip check failed: {pip_detail}", {"pip": pip_detail}
    return True, f"{len(REQUIRED_IMPORTS)} canonical imports + pip check OK", {"pip": pip_detail}


@contextmanager
def _local_verification_page() -> Iterator[str]:
    """Serve a deterministic page for real HTTP/browser verification."""
    marker = "HERMUS_LOCAL_VERIFICATION_OK"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            body = (
                "<!doctype html><html><head><title>Hermus local verification</title></head>"
                f"<body><main id='hermus-verification'>{marker}</main></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="hermus-doctor-page", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _verify_scrapling(local_url: Optional[str], *, required: bool) -> dict[str, Any]:
    if not required:
        return _subsystem("Scrapling", OPTIONAL_UNAVAILABLE, "web subsystem disabled by configuration")
    if not local_url:
        return _subsystem(
            "Scrapling", MISSING_BROKEN,
            "local verification page could not be started", required=True,
            hint="Check that loopback binding is available, then re-run setup.sh",
        )
    try:
        from core.config import config
        from core.web.gateway import WebGateway
        from core.web import capabilities

        # The production policy remains private-address blocking. Only this
        # isolated, temporary Doctor gateway permits its own loopback fixture;
        # no global config or user setting is changed.
        local_config = config.model_copy(update={
            "web_allow_private_addresses": True,
            "web_cache_enabled": False,
            "web_default_strategy": "static",
        })
        result = WebGateway(local_config).fetch(
            local_url, strategy="static", want_markdown=False,
            include_html=False, use_cache=False,
        )
        text = result.text or ""
        if not result.ok or "HERMUS_LOCAL_VERIFICATION_OK" not in text:
            return _subsystem(
                "Scrapling", MISSING_BROKEN,
                f"real static fetch failed: {result.error or result.error_code or 'marker missing'}",
                required=True,
                hint="Install/repair Scrapling with: pip install -r requirements.txt",
                evidence={"ok": result.ok, "error_code": result.error_code, "status_code": result.status_code},
            )
        capabilities.mark_verified("static")
        return _subsystem(
            "Scrapling", INSTALLED_VERIFIED,
            f"canonical WebGateway static fetch returned the local marker (HTTP {result.status_code})",
            required=True,
            evidence={"strategy": "static", "status_code": result.status_code, "source": result.source},
        )
    except Exception as exc:  # noqa: BLE001
        return _subsystem(
            "Scrapling", MISSING_BROKEN,
            f"canonical Scrapling/WebGateway smoke failed: {type(exc).__name__}: {exc}",
            required=True,
            hint="pip install -r requirements.txt and re-run setup.sh",
        )


def _verify_browser(local_url: Optional[str], *, required: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """Exercise the existing browser tool and always close its browser."""
    if not required:
        optional = _subsystem("Chromium", OPTIONAL_UNAVAILABLE, "browser strategy disabled or unavailable on this platform")
        return optional, _subsystem("Browser navigation", OPTIONAL_UNAVAILABLE, "browser strategy disabled or unavailable on this platform")
    if not local_url:
        failure = _subsystem("Chromium", MISSING_BROKEN, "local verification page could not be started", required=True)
        return failure, _subsystem("Browser navigation", MISSING_BROKEN, "local verification page could not be started", required=True)

    chromium_path = None
    try:
        from core.web import capabilities

        chromium_path = capabilities.chromium_executable_path()
    except Exception as exc:  # noqa: BLE001
        chromium_lookup_error = f"Chromium discovery failed: {type(exc).__name__}: {exc}"
    else:
        chromium_lookup_error = ""

    if chromium_path is None:
        failure = _subsystem(
            "Chromium", MISSING_BROKEN,
            chromium_lookup_error or "Playwright is installed but no Chromium executable was discovered",
            required=True,
            hint="Run: .venv/bin/python -m playwright install chromium",
        )
        return failure, _subsystem(
            "Browser navigation", MISSING_BROKEN,
            "Chromium executable discovery failed; launch was not attempted",
            required=True,
            hint="Install the Chromium browser for the selected .venv, then re-run setup.sh",
        )

    browser_module = None
    navigation = None
    close_result: dict[str, Any] = {"success": True}
    try:
        # This is the existing Hermus browser boundary, not a second browser
        # implementation. It uses Playwright and exposes browser_close.
        from tools import browser as browser_module

        if not browser_module.PLAYWRIGHT_AVAILABLE:
            navigation = {"success": False, "error": "tools.browser could not import Playwright"}
        else:
            navigation = browser_module.browser_navigate(local_url)
            if navigation.get("success"):
                extracted = browser_module.browser_extract("body")
                if not extracted.get("success") or "HERMUS_LOCAL_VERIFICATION_OK" not in str(extracted.get("text", "")):
                    navigation = {
                        "success": False,
                        "error": "browser reached the page but the expected content marker was not present",
                    }
                else:
                    navigation["content_verified"] = True
    except Exception as exc:  # noqa: BLE001
        navigation = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if browser_module is not None:
            try:
                close_result = browser_module.browser_close()
            except Exception as exc:  # noqa: BLE001
                close_result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    if not navigation or not navigation.get("success"):
        reason = (navigation or {}).get("error") or "unknown browser navigation failure"
        return (
            _subsystem(
                "Chromium", INSTALLED_UNVERIFIED,
                f"executable discovered at {chromium_path}, but launch/navigation failed: {reason}",
                required=True,
                hint="Repair Chromium/system browser dependencies and re-run setup.sh",
                evidence={"executable": str(chromium_path), "navigation": navigation, "closed": close_result},
            ),
            _subsystem(
                "Browser navigation", MISSING_BROKEN,
                f"headless launch/navigation failed: {reason}",
                required=True,
                hint="Repair Chromium/system browser dependencies and re-run setup.sh",
                evidence={"executable": str(chromium_path), "navigation": navigation, "closed": close_result},
            ),
        )

    if not close_result.get("success"):
        reason = str(close_result.get("error") or "browser_close returned failure")
        return (
            _subsystem("Chromium", MISSING_BROKEN, f"browser content verified but clean shutdown failed: {reason}", required=True),
            _subsystem("Browser navigation", MISSING_BROKEN, f"clean browser shutdown failed: {reason}", required=True),
        )

    return (
        _subsystem(
            "Chromium", INSTALLED_VERIFIED,
            f"Playwright executable discovered and headless Chromium launched, served content, and closed cleanly",
            required=True,
            evidence={"executable": str(chromium_path), "title": navigation.get("title", "")},
        ),
        _subsystem(
            "Browser navigation", INSTALLED_VERIFIED,
            "existing Hermus browser tool navigated to a local page and verified its content marker; shutdown was clean",
            required=True,
            evidence={"url": local_url, "title": navigation.get("title", ""), "content_verified": True},
        ),
    )


def _verify_gateway_and_queue() -> tuple[dict[str, Any], dict[str, Any]]:
    """Start the real FastAPI lifespan in-process and exercise core routes."""
    try:
        from fastapi.testclient import TestClient
        from gateway.gateway import app
        from gateway.queue import job_queue
    except Exception as exc:  # noqa: BLE001
        failure = f"gateway import failed: {type(exc).__name__}: {exc}"
        return (
            _subsystem("Gateway", MISSING_BROKEN, failure, required=True,
                       hint="Repair gateway dependencies with: pip install -r requirements.txt"),
            _subsystem("JobQueue", MISSING_BROKEN, failure, required=True,
                       hint="Repair queue/gateway dependencies with: pip install -r requirements.txt"),
        )

    routes = (
        "/control", "/api/status", "/api/v1/system/health", "/queue/status",
        "/jobs", "/presence", "/voice/status", "/speech/status", "/api/jarvis/status",
    )
    responses: dict[str, Any] = {}
    try:
        with TestClient(app) as client:
            for path in routes:
                response = client.get(path)
                responses[path] = {"status": response.status_code}
                if response.status_code >= 400:
                    responses[path]["body"] = response.text[:300]
            queue_response = client.get("/queue/status")
            try:
                queue_body = queue_response.json()
            except ValueError:
                queue_body = {}
            queue_state = (queue_body.get("queue") or {}) if isinstance(queue_body, dict) else {}
            if queue_response.status_code in (401, 403):
                # The route exists but the Doctor's TestClient has no operator
                # token. Inspect only the canonical in-process queue state; do
                # not weaken the gateway auth policy or manufacture a response.
                queue_state = {
                    "authorized": False,
                    "started": bool(getattr(job_queue, "_started", False)),
                    "enabled": bool(getattr(job_queue, "enabled", False)),
                }
            queue_started = bool(queue_state.get("started"))
            queue_enabled = bool(queue_state.get("enabled", getattr(job_queue, "enabled", False)))
            handler_count = len(queue_state.get("registered_kinds", [])) or len(getattr(job_queue, "handlers", {}))
            # The app lifespan must have started the canonical queue and expose
            # handlers when it is enabled. A deliberately disabled queue is a
            # valid configured mode.
            if queue_enabled and (not queue_started or handler_count == 0):
                queue_detail = (
                    f"/queue/status reported an incomplete canonical queue: started={queue_started}, "
                    f"handlers={handler_count}, state={queue_state}"
                )
                queue_result = _subsystem("JobQueue", MISSING_BROKEN, queue_detail, required=True,
                                         hint="Check queue startup logs and HERMUS_QUEUE_* configuration",
                                         evidence={"queue": queue_state, "handler_count": handler_count})
            elif not queue_enabled:
                queue_result = _subsystem("JobQueue", OPTIONAL_UNAVAILABLE, "queue disabled by HERMUS_QUEUE_ENABLED=0")
            else:
                queue_result = _subsystem(
                    "JobQueue", INSTALLED_VERIFIED,
                    f"canonical JobQueue initialized by the FastAPI lifespan with {handler_count} handlers",
                    required=True, evidence={"queue": queue_state, "handler_count": handler_count},
                )
    except Exception as exc:  # noqa: BLE001
        reason = f"gateway lifespan/route verification failed: {type(exc).__name__}: {exc}"
        return (
            _subsystem("Gateway", MISSING_BROKEN, reason, required=True,
                       hint="Read the gateway traceback, repair the reported dependency, and re-run setup.sh",
                       evidence={"responses": responses}),
            _subsystem("JobQueue", MISSING_BROKEN, reason, required=True,
                       hint="Read the gateway traceback, repair the reported dependency, and re-run setup.sh",
                       evidence={"responses": responses}),
        )

    acceptable_statuses = {200, 401, 403}
    bad = {path: info for path, info in responses.items() if info.get("status", 0) not in acceptable_statuses}
    gateway_result = _subsystem(
        "Gateway",
        MISSING_BROKEN if bad else INSTALLED_VERIFIED,
        f"FastAPI lifespan started and {len(routes) - len(bad)}/{len(routes)} required internal routes returned an expected status"
        + (f"; failures={bad}" if bad else ""),
        required=True,
        hint="Repair the failing route/runtime shown above" if bad else "",
        evidence={"routes": responses},
    )
    return gateway_result, queue_result


def _verify_computer() -> dict[str, Any]:
    try:
        from core.computer import ComputerActionController, ComputerAgent, detect_computer_capability

        controller = ComputerActionController()
        agent = ComputerAgent(controller=controller)
        capability = detect_computer_capability(controller)
        if capability.get("available"):
            status = INSTALLED_VERIFIED
            detail = "ComputerAgent and real mouse/keyboard/window backends initialized"
        else:
            status = INSTALLED_UNVERIFIED
            detail = (
                "ComputerAgent and safe dry-run backends initialized; real desktop control is unavailable: "
                f"{capability.get('reason') or 'no display or optional desktop backend'}"
            )
        return _subsystem(
            "Computer agent", status, detail,
            # Desktop control is an optional host capability. A headless server
            # must not be reported as a broken core install merely because the
            # safe ComputerAgent constructor fell back to dry-run backends.
            required=bool(capability.get("available")),
            hint="Install desktop-control packages and run under a graphical session for real control" if not capability.get("available") else "",
            evidence={"backend": capability, "agent": type(agent).__name__},
        )
    except Exception as exc:  # noqa: BLE001
        return _subsystem(
            "Computer agent", MISSING_BROKEN,
            f"ComputerAgent/backend initialization failed: {type(exc).__name__}: {exc}",
            required=True,
            hint="Repair core computer dependencies with: pip install -r requirements.txt",
        )


def _verify_voice() -> dict[str, Any]:
    try:
        from core.config import config
        from core.speech import speech_engine
        from tools.voice import FASTER_WHISPER_AVAILABLE, voice_available_models

        if not getattr(config, "voice_enabled", True):
            return _subsystem("Voice", OPTIONAL_UNAVAILABLE, "voice feature disabled by configuration")
        speech = speech_engine.status()
        models = voice_available_models()
        if not FASTER_WHISPER_AVAILABLE:
            return _subsystem(
                "Voice", OPTIONAL_UNAVAILABLE,
                "voice routes loaded, but optional faster-whisper is unavailable; no local STT model was exercised",
                hint="Install faster-whisper or configure an available local STT backend, then re-run setup.sh",
                evidence={"speech": speech, "models": models},
            )
        # Importing faster-whisper is proof of the package only. Loading a user
        # selected model downloads weights and is intentionally not an installer
        # side effect, so this remains an honest warning.
        if speech.get("available"):
            detail = "faster-whisper and a local TTS backend are installed; model/audio path remains user-configured"
        else:
            detail = "faster-whisper is installed; no local TTS backend/model is configured for synthesis"
        return _subsystem(
            "Voice", INSTALLED_UNVERIFIED, detail,
            hint="Select/download a voice model or configure HERMUS_TTS_BACKEND/Piper/eSpeak when desired",
            evidence={"faster_whisper": True, "speech": speech, "models": models},
        )
    except Exception as exc:  # noqa: BLE001
        return _subsystem("Voice", OPTIONAL_UNAVAILABLE,
                          f"optional voice capability unavailable: {type(exc).__name__}: {exc}")


def _verify_model_provider() -> dict[str, Any]:
    try:
        from core.config import config
        from core.model_capabilities import negotiate
        from core.models import get_model_gateway

        gateway = get_model_gateway()
        providers = gateway.providers(probe=False)
        configured = [p for p in providers if p.get("configured")]
        capability = negotiate(str(config.model), probe=True)
        if capability.reachable is True and capability.present is not False:
            status = INSTALLED_VERIFIED
            detail = f"configured model provider {capability.provider}/{capability.name} is reachable"
        elif capability.reachable is False:
            status = INSTALLED_UNVERIFIED
            detail = (
                f"model gateway initialized, but {capability.provider}/{capability.name} is not reachable; "
                "installation does not start Ollama or download model weights"
            )
        elif configured:
            status = INSTALLED_UNVERIFIED
            detail = f"{len(configured)} provider configuration(s) discovered; network/model health was not proven for hosted credentials"
        else:
            status = OPTIONAL_UNAVAILABLE
            detail = "no model provider credentials/runtime configured"
        return _subsystem(
            "LLM/model providers", status, detail,
            hint="Start Ollama and pull the selected model, or configure a provider in .env / the Control Room",
            evidence={
                "selected_model": str(config.model),
                "configured_providers": [p.get("provider") for p in configured],
                "selected_capability": capability.to_dict(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _subsystem("LLM/model providers", MISSING_BROKEN,
                          f"model/provider discovery failed: {type(exc).__name__}: {exc}",
                          hint="Repair model gateway dependencies and configuration")


def _verify_storage_config() -> dict[str, Any]:
    try:
        from core.config import config
        from core.memory import get_memory

        paths = [
            config.resolve_path(config.memory_db_path),
            config.resolve_path(config.memory2_db_path),
            config.resolve_path(config.embeddings_db_path),
            config.resolve_path("data/web_read_cache.db"),
        ]
        memory_stats = get_memory().index_stats()
        integrity: dict[str, str] = {}
        for path in paths:
            if not path.exists():
                continue
            # Read-only URI: the Doctor never repairs, truncates, or rewrites a
            # user database while checking its integrity.
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
            try:
                row = conn.execute("PRAGMA integrity_check").fetchone()
                integrity[str(path)] = str(row[0] if row else "unknown")
            finally:
                conn.close()
        bad = {p: state for p, state in integrity.items() if state.lower() != "ok"}
        if bad:
            return _subsystem("Storage/config", MISSING_BROKEN, f"SQLite integrity check failed: {bad}", required=True)
        return _subsystem(
            "Storage/config", INSTALLED_VERIFIED,
            f"configuration loaded; writable data paths are available; checked {len(integrity)} existing SQLite database(s)",
            required=True,
            evidence={"memory_index": memory_stats, "sqlite_integrity": integrity, "base_dir": str(config.base_dir)},
        )
    except Exception as exc:  # noqa: BLE001
        return _subsystem("Storage/config", MISSING_BROKEN,
                          f"configuration/storage initialization failed: {type(exc).__name__}: {exc}",
                          required=True,
                          hint="Check data/ permissions and database integrity; user files were not deleted")


def _verify_security() -> dict[str, Any]:
    try:
        from core.config import config
        from core.safety_policy import load_safety_policy
        from core.web.errors import SecurityBlockedError
        from core.web.security import WebSecurityPolicy

        policy = load_safety_policy()
        web_policy = WebSecurityPolicy.from_config(config)
        failures: list[str] = []
        if len(policy.rules) < 11:
            failures.append(f"red-line policy has {len(policy.rules)} rules; expected at least 11")
        if not bool(getattr(config, "permissions_enforce", True)):
            failures.append("HERMUS_PERMISSIONS_ENFORCE is disabled")
        try:
            web_policy.check("http://127.0.0.1:9/")
        except SecurityBlockedError:
            pass
        else:
            failures.append("web security policy did not block loopback/private address")
        try:
            web_policy.check("file:///etc/passwd")
        except SecurityBlockedError:
            pass
        else:
            failures.append("web security policy did not block file:// scheme")
        try:
            web_policy.check("https://user:pass@example.com/")
        except SecurityBlockedError:
            pass
        else:
            failures.append("web security policy did not block URL credentials")
        if failures:
            return _subsystem("Security", MISSING_BROKEN, "; ".join(failures), required=True,
                              hint="Restore the existing permission/SSRF/red-line protections before running Hermus")
        return _subsystem(
            "Security", INSTALLED_VERIFIED,
            f"red-line policy ({len(policy.rules)} rules), permission enforcement, URL scheme/credential/SSRF guards verified",
            required=True,
            evidence={"red_line_policy": policy.name, "private_addresses_allowed": web_policy.allow_private_addresses},
        )
    except Exception as exc:  # noqa: BLE001
        return _subsystem("Security", MISSING_BROKEN,
                          f"security verification failed: {type(exc).__name__}: {exc}", required=True,
                          hint="Do not disable security settings to make setup pass")


def _verify_sandbox() -> dict[str, Any]:
    try:
        from core.sandbox import sandbox

        state = sandbox.status()
        return _subsystem(
            "Sandbox", INSTALLED_VERIFIED,
            f"sandbox policy initialized with backend={state.get('backend')} (container runtime is optional)",
            evidence={"backend": state.get("backend"), "configured": state.get("configured"), "policy": state.get("policy")},
        )
    except Exception as exc:  # noqa: BLE001
        return _subsystem("Sandbox", OPTIONAL_UNAVAILABLE,
                          f"optional sandbox backend unavailable: {type(exc).__name__}: {exc}")


def _verify_android() -> dict[str, Any]:
    try:
        from core.web.capabilities import is_termux

        if not is_termux():
            return _subsystem("Android/Termux", OPTIONAL_UNAVAILABLE, "not an Android/Termux host")
        import core.android  # noqa: F401

        return _subsystem("Android/Termux", INSTALLED_VERIFIED,
                          "Android companion/control package imports; live device transport requires explicit pairing",
                          evidence={"termux": True})
    except Exception as exc:  # noqa: BLE001
        return _subsystem("Android/Termux", OPTIONAL_UNAVAILABLE,
                          f"Android/Termux integration unavailable: {type(exc).__name__}: {exc}")


def _verify_host_runtime() -> dict[str, Any]:
    """Verify host commands used by updates/downloads and report optional media."""
    git_ok, git_detail = _command_check("git", "--version")
    curl_ok, curl_detail = _command_check("curl", "--version")
    ffmpeg_ok, ffmpeg_detail = _command_check("ffmpeg", "-version")
    ssh_ok, ssh_detail = _command_check("ssh", "-V")
    required_ok = git_ok and curl_ok
    detail = f"git={git_detail}; curl={curl_detail}; "
    detail += f"ffmpeg={ffmpeg_detail if ffmpeg_ok else 'system command unavailable (imageio-ffmpeg fallback is checked separately)'}; "
    detail += f"ssh={ssh_detail if ssh_ok else 'optional/unavailable'}"
    return _subsystem(
        "Host runtimes", INSTALLED_VERIFIED if required_ok else MISSING_BROKEN,
        detail, required=True,
        hint="Install Git and curl, then re-run setup.sh" if not required_ok else "",
        evidence={"git": git_ok, "curl": curl_ok, "ffmpeg": ffmpeg_ok, "ssh": ssh_ok,
                  "bundled_ffmpeg": _ffmpeg_available()},
    )


def _verify_node_runtime() -> dict[str, Any]:
    node_ok, node_detail = _command_check("node", "--version")
    npm_ok, npm_detail = _command_check("npm", "--version")
    bun_ok, bun_detail = _command_check("bun", "--version")
    if node_ok and npm_ok:
        bun_note = f"; bun {bun_detail}" if bun_ok else "; bun unavailable (optional)"
        return _subsystem("Node/npm", INSTALLED_VERIFIED, f"node {node_detail}; npm {npm_detail}{bun_note}")
    return _subsystem("Node/npm", OPTIONAL_UNAVAILABLE,
                      f"Node/npm is optional for this Python-only checkout (node: {node_detail}; npm: {npm_detail}; bun: {bun_detail})")


def _deep_subsystems() -> dict[str, dict[str, Any]]:
    """Compose deep checks while keeping all capability logic in Doctor."""
    try:
        from core.web.capabilities import is_termux
    except Exception as exc:  # pragma: no cover - damaged dependency path
        is_termux = lambda: False  # type: ignore[assignment]
        capability_import_error = f"web capability import failed: {type(exc).__name__}: {exc}"
    else:
        capability_import_error = ""
    try:
        from core.config import config
    except Exception as exc:  # pragma: no cover - damaged checkout/config path
        config = None
        config_import_error = f"configuration import failed: {type(exc).__name__}: {exc}"
    else:
        config_import_error = ""

    out: dict[str, dict[str, Any]] = {}

    py_detail = f"{sys.executable} · Python {platform.python_version()}"
    if sys.version_info < (3, 10):
        out["python_venv"] = _subsystem("Python/venv", MISSING_BROKEN, f"{py_detail}; Python 3.10+ required", required=True,
                                        hint="Install Python 3.10 or newer and re-run setup.sh")
    elif not _interpreter_is_project_venv():
        out["python_venv"] = _subsystem("Python/venv", MISSING_BROKEN,
                                        f"{py_detail}; process is not using {REPO_ROOT / '.venv'}",
                                        required=True,
                                        hint="Run setup.sh or activate .venv before running Hermus")
    else:
        pip_ok, pip_detail = _command_check(sys.executable, "-m", "pip", "--version")
        out["python_venv"] = _subsystem(
            "Python/venv", INSTALLED_VERIFIED if pip_ok else MISSING_BROKEN,
            f"{py_detail}; project .venv active; {pip_detail}", required=True,
            hint="Repair .venv with: python3 -m venv .venv" if not pip_ok else "",
        )

    out["host_runtimes"] = _verify_host_runtime()

    dep_ok, dep_detail, dep_evidence = _dependency_runtime_check()
    out["dependencies"] = _subsystem(
        "Dependencies", INSTALLED_VERIFIED if dep_ok else MISSING_BROKEN,
        dep_detail, required=True,
        hint="Run: .venv/bin/python -m pip install -r requirements.txt" if not dep_ok else "",
        evidence=dep_evidence,
    )

    web_required = config is None or bool(getattr(config, "web_enabled", True))
    browser_required = web_required and bool(getattr(config, "web_dynamic_enabled", True)) and not is_termux()
    try:
        with _local_verification_page() as local_url:
            out["scrapling"] = _verify_scrapling(local_url, required=web_required)
            chromium, browser = _verify_browser(local_url, required=browser_required)
            out["chromium"] = chromium
            out["browser_navigation"] = browser
    except Exception as exc:  # noqa: BLE001 - loopback failure must become report rows
        reason = f"local verification fixture could not start: {type(exc).__name__}: {exc}"
        out["scrapling"] = _subsystem(
            "Scrapling", MISSING_BROKEN if web_required else OPTIONAL_UNAVAILABLE,
            reason, required=web_required,
            hint="Check loopback binding/permissions and re-run setup.sh" if web_required else "",
        )
        browser_status = MISSING_BROKEN if browser_required else OPTIONAL_UNAVAILABLE
        out["chromium"] = _subsystem("Chromium", browser_status, reason, required=browser_required)
        out["browser_navigation"] = _subsystem("Browser navigation", browser_status, reason, required=browser_required)

    gateway, queue = _verify_gateway_and_queue()
    out["gateway"] = gateway
    out["job_queue"] = queue
    out["computer_agent"] = _verify_computer()
    out["voice"] = _verify_voice()
    out["model_provider"] = _verify_model_provider()
    out["storage_config"] = _verify_storage_config()
    out["security"] = _verify_security()
    out["sandbox"] = _verify_sandbox()
    out["node"] = _verify_node_runtime()
    out["android"] = _verify_android()

    # A single deep, real dependency check is the Core runtime evidence. It is
    # intentionally based on the results above rather than a second import list.
    core_required = (out["python_venv"]["ok"] and out["host_runtimes"]["ok"] and
                     out["dependencies"]["ok"] and out["gateway"]["ok"] and
                     out["storage_config"]["ok"] and out["security"]["ok"])
    import_errors = "; ".join(error for error in (capability_import_error, config_import_error) if error)
    core_detail = (
        "Hermus core, configuration, tool/gateway lifecycle, storage and security probes completed"
        if core_required else "one or more required runtime probes failed; see the detailed rows above"
    )
    if import_errors:
        core_detail = f"{core_detail}; {import_errors}"
    out["core_runtime"] = _subsystem(
        "Core runtime", INSTALLED_VERIFIED if core_required else MISSING_BROKEN,
        core_detail,
        required=True,
    )
    # The tool-system check is deliberately after the agent/gateway imports so
    # it verifies the canonical registry/gateway actually exposes descriptors.
    try:
        from core.tools import get_tool_gateway

        tool_gateway = get_tool_gateway()
        descriptors = tool_gateway.descriptors()
        out["tool_system"] = _subsystem(
            "Tool system", INSTALLED_VERIFIED if descriptors else MISSING_BROKEN,
            f"canonical ToolGateway initialized with {len(descriptors)} descriptors",
            required=True,
            hint="Repair tool registration/import errors shown by tool_registry" if not descriptors else "",
            evidence={"count": len(descriptors)},
        )
    except Exception as exc:  # noqa: BLE001
        out["tool_system"] = _subsystem("Tool system", MISSING_BROKEN,
                                        f"ToolGateway initialization failed: {type(exc).__name__}: {exc}", required=True)

    # Configuration is represented by the storage/config check; expose the
    # requested human-facing label without another probe.
    out["configuration"] = _subsystem(
        "Configuration loading", INSTALLED_VERIFIED if out["storage_config"]["ok"] else MISSING_BROKEN,
        "core.config loaded and resolved project paths" if out["storage_config"]["ok"] else out["storage_config"]["detail"],
        required=True,
    )
    # Agent initialization is a real constructor call, not a mock response.
    try:
        from core.agent import HermusAgent

        agent = HermusAgent(model=config.model, mode="chat", max_steps=1)
        out["agent_startup"] = _subsystem(
            "Core agent", INSTALLED_VERIFIED,
            f"HermusAgent initialized through ModelGateway with {len(agent.tools)} registered tools",
            required=True,
            evidence={"model": str(config.model), "tool_count": len(agent.tools)},
        )
    except Exception as exc:  # noqa: BLE001
        out["agent_startup"] = _subsystem("Core agent", MISSING_BROKEN,
                                          f"HermusAgent startup failed: {type(exc).__name__}: {exc}", required=True,
                                          hint="Repair model/tool imports; no model request was sent by setup")

    return out


def print_diagnostics(report: dict[str, Any]) -> None:
    """Print the normal Doctor report, plus deep subsystem statuses if present."""
    print("=" * 60)
    print("Hermus Doctor — health report")
    print("=" * 60)
    for check in report["checks"]:
        if "status" in check:
            # Deep setup rows are printed in the ordered subsystem section below.
            continue
        mark = "✅" if check["ok"] else ("⚠️" if check["level"] == "recommended" else "❌")
        print(f"  {mark} {check['name']:<18} {check['detail']}")
        if not check["ok"] and check["hint"]:
            print(f"        → {check['hint']}")
    if report.get("subsystems"):
        print("-" * 60)
        print("  INSTALLATION / OPERATIONAL SUBSYSTEMS")
        for _key, item in report["subsystems"].items():
            print(f"  {item['status']} {item['name']}: {item['detail']}")
            if item.get("hint") and not item["ok"]:
                print(f"        → {item['hint']}")
    o = report["overall"]
    print("=" * 60)
    print(f"  Required: {'OK' if o['required'] else 'FAIL'}   "
          f"Recommended: {'OK' if o['recommended'] else 'IMPROVE'}   "
          f"({o['passed']}/{o['total']} checks pass)")
    print("=" * 60)


__all__ = [
    "INSTALLED_VERIFIED", "INSTALLED_UNVERIFIED", "MISSING_BROKEN", "OPTIONAL_UNAVAILABLE",
    "run_diagnostics", "print_diagnostics",
]
