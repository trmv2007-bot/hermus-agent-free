"""A second `gateway start` says so and stops; `status` says what is there.

The defect: with the port already bound, `hermus gateway start` reached uvicorn,
failed to bind, and left a live process holding no listener — a launcher clicked
twice, or a second start for a test, left a process nobody could see or account
for. These tests hold real sockets and drive the real CLI, so the refusal is
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


@pytest.fixture()
def non_http_port():
    """A port answering with a banner no HTTP parser accepts, like an SSH server."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    sock.settimeout(0.5)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except (TimeoutError, OSError):
                continue
            try:
                conn.sendall(b"SSH-2.0-not-a-gateway\r\n")
            except OSError:
                pass
            finally:
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield sock.getsockname()[1]
    finally:
        stop.set()
        sock.close()
        thread.join(5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _lan_ipv4s() -> list[str]:
    """This machine's non-loopback IPv4 addresses, routable ones before link-local.

    A disconnected adapter keeps a 169.254.x.x address that nothing can bind, so the
    order matters: the routable address of a real interface is what a gateway on the
    LAN would actually use.
    """
    try:
        import psutil
    except ImportError:
        return []
    routable: list[str] = []
    link_local: list[str] = []
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family != socket.AF_INET or not addr.address or addr.address.startswith("127."):
                continue
            (link_local if addr.address.startswith("169.254.") else routable).append(addr.address)
    return routable + link_local


def _listen_on_lan():
    """A listening socket on a LAN address, or None when this machine has none."""
    for address in _lan_ipv4s():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((address, 0))
        except OSError:
            sock.close()
            continue
        sock.listen(8)
        return sock, address
    return None


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        body = json.dumps({"success": True, "status": "ok", "pid": os.getpid()}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _answering(host: str) -> HTTPServer:
    """A server answering /healthz the way the gateway does."""
    server = HTTPServer((host, 0), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _answering_on_lan() -> HTTPServer | None:
    """The same server bound to a LAN address, or None when this machine has none."""
    for address in _lan_ipv4s():
        try:
            return _answering(address)
        except OSError:
            continue
    return None


@pytest.fixture()
def answering_gateway():
    server = _answering("127.0.0.1")
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


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


def test_a_free_ipv6_host_reads_as_free():
    """An IPv6 bind address is not an address-in-use; the probe must not say it is."""
    assert ports.port_in_use(_free_port(), "::1") is False


def test_a_loopback_ipv6_listener_is_seen():
    if not socket.has_ipv6:
        pytest.skip("this machine has no IPv6")
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        sock.bind(("::1", 0))
    except OSError:
        pytest.skip("this machine has no IPv6 loopback")
    sock.listen(8)
    try:
        assert ports.port_in_use(sock.getsockname()[1]) is True
    finally:
        sock.close()


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


def test_status_reports_a_running_gateway(tmp_path, answering_gateway):
    running, line = ports.gateway_status(answering_gateway)
    assert running is True
    assert f"port {answering_gateway}" in line
    assert str(os.getpid()) in line

    done = _cli("gateway", "status", "--port", str(answering_gateway), home=tmp_path)
    assert done.returncode == 0
    assert str(os.getpid()) in done.stdout


def test_status_finds_a_gateway_bound_to_a_lan_address():
    """A gateway on a LAN interface answers there and not on loopback; status must find it."""
    server = _answering_on_lan()
    if server is None:
        pytest.skip("this machine has no bindable non-loopback IPv4 address")
    try:
        running, line = ports.gateway_status(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()

    assert running is True, line
    assert str(os.getpid()) in line


def test_a_lan_only_listener_reads_as_taken_without_the_process_table(monkeypatch):
    """A gateway bound only to 192.168.x.x must still be seen: loopback never answers it."""
    held = _listen_on_lan()
    if held is None:
        pytest.skip("this machine has no bindable non-loopback IPv4 address")
    sock, _ = held
    monkeypatch.setattr(ports, "listener_pid", lambda port: None)
    try:
        assert ports.port_in_use(sock.getsockname()[1]) is True
    finally:
        sock.close()


def test_the_second_start_refuses_a_lan_only_listener(tmp_path):
    held = _listen_on_lan()
    if held is None:
        pytest.skip("this machine has no bindable non-loopback IPv4 address")
    sock, _ = held
    port = sock.getsockname()[1]
    try:
        done = _cli("gateway", "start", "--port", str(port), home=tmp_path, timeout=120)
    finally:
        sock.close()

    assert done.returncode != 0, done.stdout + done.stderr
    assert str(port) in done.stdout
    assert "already in use" in done.stdout


def test_status_finds_a_lan_gateway_when_the_owner_pid_cannot_be_named(monkeypatch):
    """macOS refuses net_connections to a normal user; status must still find the gateway."""
    server = _answering_on_lan()
    if server is None:
        pytest.skip("this machine has no bindable non-loopback IPv4 address")
    monkeypatch.setattr(ports, "listener_pid", lambda port: None)
    try:
        running, line = ports.gateway_status(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()

    assert running is True, line


def _only_ipv4(monkeypatch):
    """An environment with IPv6 switched off: an AF_INET6 socket cannot be made."""
    real_socket = socket.socket

    def only_ipv4(family=socket.AF_INET, *args, **kwargs):
        if family == socket.AF_INET6:
            raise OSError(10047, "Address family not supported by protocol")
        return real_socket(family, *args, **kwargs)

    monkeypatch.setattr(socket, "socket", only_ipv4)


def test_an_ipv6_host_without_ipv6_is_not_reported_free(monkeypatch):
    """Where the kernel cannot try the address, the port is unknown, not free."""
    port = _free_port()
    _only_ipv4(monkeypatch)
    assert ports.port_in_use(port, "::1") is True


def test_an_ipv6_host_without_ipv6_refuses_in_one_line(monkeypatch, capsys):
    """An environment error is refused with its own reason, not 'already in use'."""
    port = _free_port()
    _only_ipv4(monkeypatch)

    with pytest.raises(SystemExit) as refused:
        ports.require_free(port, "::1")

    assert refused.value.code == 1
    message = capsys.readouterr().out.strip()
    assert len(message.splitlines()) == 1
    assert "::1" in message and str(port) in message
    assert "cannot listen" in message
    assert "already in use" not in message


def test_status_names_a_foreign_process_on_the_port(held_port):
    running, line = ports.gateway_status(held_port)

    assert running is False
    assert f"not answering on port {held_port}" in line
    assert str(os.getpid()) in line


def test_status_survives_a_listener_that_is_not_http(tmp_path, non_http_port):
    running, line = ports.gateway_status(non_http_port)

    assert running is False
    assert f"not answering on port {non_http_port}" in line

    done = _cli("gateway", "status", "--port", str(non_http_port), home=tmp_path)
    assert done.returncode == 1
    assert "Traceback" not in done.stderr
    assert f"port {non_http_port}" in done.stdout
