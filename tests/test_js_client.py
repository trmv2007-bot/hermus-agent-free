"""Run the browser-client JS tests from pytest.

``tests/js/*.test.js`` had no runner: there is no package.json and no documented
`node --test` invocation, so nothing ever executed them. That is how
``gateway/static/hermus-client.js`` could be deleted while its test still
required it — the test simply never ran. Wiring it into pytest means a missing
or broken client module fails the suite instead of going unnoticed.

Skipped (not failed) when Node is unavailable, so the Python suite stays green on
machines without a JS toolchain.

Note the glob: `node --test tests/js/` treats the bare directory as a module path
and dies with MODULE_NOT_FOUND on Node 22. The file pattern is required.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "tests" / "js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_client_js_tests_pass():
    patterns = sorted(p.name for p in JS_DIR.glob("*.test.js"))
    assert patterns, "expected at least one JS test file under tests/js"

    result = subprocess.run(
        ["node", "--test", *(f"tests/js/{name}" for name in patterns)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, f"JS client tests failed:\n{output[-4000:]}"
    assert "# fail 0" in output, f"JS client tests reported failures:\n{output[-4000:]}"


def test_client_module_exists_and_is_served_by_the_gateway():
    """The file the JS tests require must exist and be reachable over HTTP.

    Guards the exact regression that happened before: a test requiring a client
    module that is not on disk, and no route serving it.
    """
    client_path = ROOT / "gateway" / "static" / "control-client.js"
    assert client_path.is_file(), f"missing browser client module: {client_path}"

    import gateway.gateway as gw

    found = []

    def collect(routes):
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None:
                collect(inner.routes)
                continue
            path = getattr(r, "path", None)
            if path is not None:
                found.append(path)

    collect(gw.app.routes)
    assert "/static/control-client.js" in found, "client script route is not mounted"
