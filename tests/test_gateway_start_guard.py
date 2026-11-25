"""A second `gateway start` says so and stops; `status` says what is there.

The defect: with the port already bound, `hermus gateway start` reached uvicorn,
failed to bind, and left a live process holding no listener — a launcher clicked
twice, or a second start for a test, left a process nobody could see or account
for. These tests hold a real socket and drive the real CLI, so the refusal is
proven where a person meets it, and no test process is left running.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from gateway import ports

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def held_port():
    """A port this test process is really listening on."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 0))
    sock.listen(8)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture()
def loopback_port():
    """A port held on loopback only, as the companion launch binds it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


def _cli(*args: str, home: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        HERMUS_HOME=str(home),
        HERMUS_DATA_DIR=str(home / "data"),
    )
    return subprocess.run(
        [sys.executable, str(ROOT / "hermus.py"), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def test_a_bound_port_reads_as_taken_and_names_its_owner(held_port):
    assert ports.port_in_use(held_port) is True
    assert ports.listener_pid(held_port) == os.getpid()


def test_a_loopback_listener_is_seen_from_a_wildcard_start(loopback_port):
    """Windows happily binds 0.0.0.0 while another holds 127.0.0.1; the check must not."""
    assert ports.port_in_use(loopback_port, "0.0.0.0") is True


def test_the_second_start_refuses_a_loopback_listener(tmp_path, loopback_port):
    done = _cli("gateway", "start", "--port", str(loopback_port), home=tmp_path, timeout=120)

    assert done.returncode != 0, done.stdout + done.stderr
    assert str(loopback_port) in done.stdout
    assert "already in use" in done.stdout


def test_a_free_port_reads_as_free():
    assert ports.port_in_use(_free_port()) is False


def test_require_free_refuses_in_one_line(capsys, held_port):
    with pytest.raises(SystemExit) as refused:
        ports.require_free(held_port)

    assert refused.value.code == 1
    message = capsys.readouterr().out
    assert str(held_port) in message
    assert "0.0.0.0" in message
    assert str(os.getpid()) in message
    assert message.strip().count("\n") == 0


def test_require_free_lets_a_free_port_through(capsys):
    ports.require_free(_free_port())
    assert capsys.readouterr().out == ""


def test_a_second_start_exits_at_once_and_leaves_one_listener(tmp_path, held_port):
    started = time.monotonic()
    done = _cli("gateway", "start", "--port", str(held_port), home=tmp_path, timeout=120)
    elapsed = time.monotonic() - started

    assert done.returncode != 0, done.stdout + done.stderr
    assert str(held_port) in done.stdout
    assert str(os.getpid()) in done.stdout
    assert elapsed < 30, f"the second start took {elapsed:.1f}s instead of refusing at once"
    assert ports.listener_pid(held_port) == os.getpid()


def test_status_reports_a_stopped_gateway(tmp_path, capsys):
    port = _free_port()

    running, line = ports.gateway_status(port)
    assert running is False
    assert f"not running on port {port}" in line

    done = _cli("gateway", "status", "--port", str(port), home=tmp_path)
    assert done.returncode == 1
    assert f"not running on port {port}" in done.stdout


@pytest.fixture()
def answering_gateway():
    """A loopback server answering /healthz the way the gateway does."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
            body = json.dumps({"success": True, "status": "ok", "pid": os.getpid()}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_status_reports_a_running_gateway(tmp_path, answering_gateway):
    running, line = ports.gateway_status(answering_gateway)
    assert running is True
    assert f"port {answering_gateway}" in line
    assert str(os.getpid()) in line

    done = _cli("gateway", "status", "--port", str(answering_gateway), home=tmp_path)
    assert done.returncode == 0
    assert str(os.getpid()) in done.stdout


def test_status_names_a_foreign_process_on_the_port(held_port, capsys):
    running, line = ports.gateway_status(held_port)

    assert running is False
    assert "another process holds it" in line
    assert str(os.getpid()) in line
