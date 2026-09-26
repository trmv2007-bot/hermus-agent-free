"""PRODUCT.md is a contract, so it needs teeth.

The failure this project actually had was not bad code - it was six competing
front ends, each built by a model that saw an incomplete project and filled the
gap. Documentation does not stop that. A failing test does.

These tests are deliberately narrow: they assert the DECISIONS in PRODUCT.md,
not the prose. If a future change contradicts the contract, CI goes red instead
of the codebase quietly growing a seventh UI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "gateway" / "static"
PRODUCT = ROOT / "PRODUCT.md"


@pytest.fixture(scope="module")
def product_text() -> str:
    assert PRODUCT.is_file(), "PRODUCT.md is the project contract and must exist"
    return PRODUCT.read_text(encoding="utf-8")


# ---------------------------------------------------------------- the contract
def test_product_md_states_the_single_surface_rule(product_text: str) -> None:
    """The core decision, in the contract, so it cannot be quietly dropped."""
    assert "single-surface" in product_text.lower()
    assert "switch applications" in product_text.lower()


def test_product_md_names_the_duplicate_uis_as_removable(product_text: str) -> None:
    """The duplicates are named by stem, so deleting them is sanctioned.

    Matched on stem (hood, jarvis-hud, ...) rather than full filename: the
    contract should not go stale when an extension changes.
    """
    low = product_text.lower()
    for stem in ("hood", "jarvis-hud", "gods-eye", "console", "control-client"):
        assert stem in low, f"{stem} is a duplicate UI but the contract does not name it"


def test_product_md_declares_workspace_is_shell_and_hermus_is_skin(product_text: str) -> None:
    assert "workspace is the shell" in product_text.lower()
    assert "hermus is the skin" in product_text.lower()


# ------------------------------------------------------- one UI, not six
DUPLICATE_ASSETS = ("hood.js", "hood.css", "jarvis-hud.js", "jarvis-hud.css", "gods-eye.js", "console.js", "control-client.js")


def test_only_one_first_party_frontend_exists() -> None:
    """One product UI. A second sibling front end is the failure this guards.

    The workspace React app is the product. control.html is a diagnostics
    drawer. Nothing else may ship a first-party front end.
    """
    survivors = [name for name in DUPLICATE_ASSETS if (STATIC / name).is_file()]
    assert not survivors, (
        f"duplicate front-end assets present: {survivors}. "
        "PRODUCT.md section 2 makes the workspace the only product UI; "
        "delete these rather than maintaining them in parallel."
    )


def test_control_html_does_not_load_parallel_front_ends() -> None:
    """control.html is a drawer. It must not bootstrap five other UIs."""
    control = ROOT / "gateway" / "control.html"
    if not control.is_file():
        pytest.skip("control.html not present in this checkout")
    html = control.read_text(encoding="utf-8", errors="replace")
    for asset in ("hood.js", "jarvis-hud.js", "gods-eye.js", "console.js", "control-client.js"):
        assert asset not in html, f"control.html still loads {asset}; the drawer must not host parallel UIs"


def test_no_stray_workspace_alias_directories() -> None:
    """One workspace. A second directory that looks like a product surface is sprawl."""
    candidates = [p for p in STATIC.iterdir() if p.is_dir()] if STATIC.is_dir() else []
    # Known non-UI directories are fine; anything UI-shaped is not.
    allowed = {"workspace", "vendor", "assets"}
    strays = [p.name for p in candidates if p.name not in allowed and re.search(r"(ui|hud|hood|jarvis|eye|console)", p.name, re.I)]
    assert not strays, f"stray UI-shaped directories under gateway/static: {strays}"


# ------------------------------------------------ honesty about capability
def test_doctor_can_report_simulated_computer_control() -> None:
    """PRODUCT.md section 4: simulated must be nameable, never reported as real."""
    doctor = ROOT / "core" / "doctor.py"
    if not doctor.is_file():
        pytest.skip("core/doctor.py not present")
    src = doctor.read_text(encoding="utf-8", errors="replace")
    assert "_analyze_computer_control" in src, "doctor must be able to distinguish real from simulated control"
    assert "computer_control_simulated" in src
    assert "computer_control_real" in src


def test_computer_control_stays_wired_into_doctor_analysis() -> None:
    """A defined-but-uncalled check is dead code, not honesty."""
    doctor = ROOT / "core" / "doctor.py"
    src = doctor.read_text(encoding="utf-8", errors="replace")
    assert "findings.extend(self._analyze_computer_control())" in src


def test_gui_extra_is_declared_so_real_control_is_installable() -> None:
    """Real control must be reachable from a clean install, not just this box."""
    pyproject = ROOT / "pyproject.toml"
    src = pyproject.read_text(encoding="utf-8")
    assert re.search(r"^gui\s*=\s*\[", src, re.M), "pyproject must declare a 'gui' extra"
    for dep in ("pyautogui", "mss", "pywinauto", "uiautomation"):
        assert dep in src, f"{dep} must be declared in the gui extra"


# ------------------------------------------------- delivery is not queueing
def test_presence_does_not_acknowledge_checkins_at_queue_time() -> None:
    """Queuing is not delivery. This was a real silent-loss bug."""
    gateway = ROOT / "gateway" / "gateway.py"
    if not gateway.is_file():
        pytest.skip("gateway/gateway.py not present")
    src = gateway.read_text(encoding="utf-8", errors="replace")
    # Ignore comments: the fix's own comment explains the old bug and names
    # mark_checkin(). Only executable code matters here.
    code = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
    assert "mark_checkin(" not in code, (
        "gateway must not mark a proactive check-in delivered at queue time; "
        "use mark_checkin_pending() and let delivery confirm."
    )
    assert "mark_checkin_pending(" in code


def test_presence_can_actually_deliver_checkins() -> None:
    """check_ins_due() computes. deliver_check_ins() is the half that tells the user."""
    presence = ROOT / "core" / "presence.py"
    src = presence.read_text(encoding="utf-8", errors="replace")
    assert "def deliver_check_ins" in src
    assert "no_sink" in src, "an absent delivery channel must be reported, not assumed"
