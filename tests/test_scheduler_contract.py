"""The room runs on one scheduler, and the page proves it structurally.

Two layers of proof:

* ``tests/js/scheduler.test.js`` exercises the scheduler itself under Node —
  one bundle request for the first screen, a bounded socket count, dedupe,
  backoff, and silence while the tab is hidden.
* The static contract below catches the regression a unit test cannot: a new
  script quietly adding its own ``setInterval`` fetcher, or the page loading a
  panel script before the scheduler it must use.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "gateway" / "static"
JS_TESTS = ROOT / "tests" / "js"

#: Scripts that paint the room. None of them may own a timer any more.
ROOM_SCRIPTS = (
    "control-room.js",
    "control-core.js",
    "control-agents.js",
    "nexus.js",
    "personal-space.js",
    "hud.js",
)


@pytest.fixture(scope="module")
def node_available():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    return True


class TestNoScriptOwnsATimer:
    def test_no_room_script_keeps_a_network_interval(self):
        offenders = {}
        for name in ROOM_SCRIPTS:
            text = (STATIC / name).read_text(encoding="utf-8")
            count = text.count("setInterval")
            if count:
                offenders[name] = count
        # hud.js keeps a documented fallback for a page without the scheduler;
        # every other script must have none at all.
        assert set(offenders) <= {"hud.js"}, f"scripts still polling on their own timers: {offenders}"

    def test_the_page_loads_the_scheduler_first(self):
        html = (ROOT / "gateway" / "control.html").read_text(encoding="utf-8")
        scheduler_at = html.index("/static/scheduler.js")
        for name in ("nexus.js", "control-room.js", "control-core.js", "control-agents.js", "personal-space.js", "hud.js"):
            assert scheduler_at < html.index(f"/static/{name}"), f"{name} loads before the scheduler"

    def test_the_gateway_serves_the_scheduler(self):
        from gateway.gateway import app

        assert any(getattr(route, "path", "") == "/static/scheduler.js" for route in app.routes)


class TestSchedulerBehaviour:
    def test_node_suite_passes(self, node_available):
        proc = subprocess.run(
            ["node", "--test", str(JS_TESTS / "scheduler.test.js")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
            timeout=120,
        )
        assert proc.returncode == 0, f"scheduler tests failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-2000:]}"


class TestFirstScreenContract:
    """The endpoints the first screen reads are all in the bundle."""

    FIRST_SCREEN_PATHS = (
        "/api/v1/system/health",
        "/api/v1/system/capabilities",
        "/presence",
        "/jobs",
        "/queue/status",
        "/missions",
        "/events/recent",
        "/emergency/status",
        "/api/v1/agents/list",
        "/api/v1/agents/messages",
        "/api/v1/agents/api-keys/list",
        "/personal-space",
        "/voice/status",
        "/api/v1/console/manifest",
    )

    def test_every_first_screen_path_is_bundle_served(self):
        text = (STATIC / "scheduler.js").read_text(encoding="utf-8")
        for path in self.FIRST_SCREEN_PATHS:
            assert f"'{path}':" in text, f"{path} is not served by the console state bundle"

    def test_diagnostics_are_not_bundle_served(self):
        text = (STATIC / "scheduler.js").read_text(encoding="utf-8")
        for path in ("/doctor/status", "/engine/status"):
            assert f"'{path}'" not in text.split("DIRECT_TTL_MS")[0], f"{path} must stay behind the diagnostics door"
            assert f"'{path}'" in text, f"{path} should still be cached when the door opens"
