"""`hermus --help` leads with three commands; `hermus` alone opens the room.

The parser knows fifty-odd commands because the repository can do that much, so
the help page is where the product and the tooling were confused with each other.
These tests hold the split:

* the first section names exactly three commands, in the order a person meets
  them, and nothing else;
* every command the parser accepts is still listed under Developers, so the
  quieter page lost nothing;
* ``hermus`` with no command runs the start path rather than the terminal chat;
* the start path opens a gateway that is already answering and never spawns a
  second one.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from hermus_cli import SPECS, build_parser
from hermus_cli import start as start_cmd


def first_section(help_text: str) -> str:
    """The text between "Start here:" and the Developers heading."""
    body = help_text.split("Start here:", 1)[1]
    return body.split("Developers -", 1)[0]


def test_the_first_section_is_exactly_the_three_normal_commands():
    body = first_section(build_parser().format_help())
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    names = [line.split("  ")[0].strip() for line in lines]
    assert names == ["hermus", "hermus undo", "hermus understand"]
    assert len(lines) == 3, f"the first section holds {len(lines)} lines: {lines}"


def test_the_three_say_what_they_do_in_plain_words():
    body = first_section(build_parser().format_help()).lower()
    for promise in ("open the room", "undo the last thing", "can and cannot do"):
        assert promise in body, f"the first section never says {promise!r}"


def test_every_command_is_still_listed_under_developers():
    text = build_parser().format_help()
    developers = text.split("Developers -", 1)[1]
    missing = [spec.name for spec in SPECS if f"  {spec.name} " not in developers]
    assert missing == [], f"{missing} disappeared from --help"


def test_hermus_alone_runs_the_start_path_not_terminal_chat(monkeypatch):
    from hermus_cli import main

    calls = []
    monkeypatch.setattr("sys.argv", ["hermus"])
    monkeypatch.setattr(start_cmd, "run", lambda args, ctx: calls.append("start"))
    monkeypatch.setattr("hermus_cli.repl.run_default", lambda args, ctx: calls.append("tui"))
    main()
    assert calls == ["start"], f"a bare `hermus` ran {calls}"


class _Ready(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's spelling
        body = b'{"status": "ready"}'
        self.send_response(200 if self.path.rstrip("/") == "/readyz" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D401 - silence the test log
        pass


@pytest.fixture()
def ready_gateway():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Ready)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_a_running_gateway_is_opened_and_never_started_twice(ready_gateway, monkeypatch, capsys):
    opened = []
    monkeypatch.setenv("HERMUS_GATEWAY_PORT", str(ready_gateway))
    monkeypatch.setattr(start_cmd, "spawn_gateway", lambda port: pytest.fail(f"spawned a second gateway on {port}"))
    monkeypatch.setattr(start_cmd.webbrowser, "open", lambda url: opened.append(url))
    start_cmd.run(None, None)
    assert opened == [f"http://127.0.0.1:{ready_gateway}/"]
    assert "already running" in capsys.readouterr().out


def test_a_port_that_is_not_a_gateway_is_not_believed():
    assert start_cmd.answering(1, timeout=0.5) is False, "a closed port answered /readyz"


# ---------------------------------------------------------------------------
# What the start path must not do: open the network, or open a room it cannot answer
# ---------------------------------------------------------------------------
def test_the_companion_listens_on_this_machine_only(monkeypatch):
    """`hermus gateway start` may bind every interface; `hermus` may not.

    A terminal chat that starts listening on 0.0.0.0 would turn one word into an
    unauthenticated control plane for the whole network, running after the window
    is closed.
    """
    seen: list[list[str]] = []

    class _Proc:
        pid = 1

    monkeypatch.setattr(start_cmd.subprocess, "Popen", lambda command, **kw: (seen.append(command), _Proc())[1])
    start_cmd.spawn_gateway(8000)
    command = seen[0]
    assert "--host" in command, f"the companion launch does not pin its interface: {command}"
    assert command[command.index("--host") + 1] == "127.0.0.1"


def test_a_gated_gateway_opens_a_room_that_can_answer(monkeypatch):
    """The root, not the room: it is what sends a fresh install to the welcome flow."""
    monkeypatch.setenv("HERMUS_GATEWAY_TOKEN", "s3cret")
    assert start_cmd.room_link(8000) == "http://127.0.0.1:8000/?token=s3cret"
    monkeypatch.delenv("HERMUS_GATEWAY_TOKEN")
    assert start_cmd.room_link(8000) == "http://127.0.0.1:8000/"


def test_a_token_with_url_syntax_survives_the_link(monkeypatch):
    """`+`, `&` and `#` in a key must not be read as a space, a separator or a fragment."""
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setenv("HERMUS_GATEWAY_TOKEN", "abc+def&ghi#jkl")
    link = start_cmd.room_link(8000)
    assert "#" not in link.split("?", 1)[1], f"the token opened a fragment: {link}"
    assert parse_qs(urlsplit(link).query)["token"] == ["abc+def&ghi#jkl"]


def test_the_token_survives_the_root_redirect(monkeypatch):
    """`/?token=…` must reach the room with the token, not drop it on the hop."""
    import asyncio

    from starlette.requests import Request

    from gateway.gateway import root

    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"token=s3cret", "headers": []}
    response = asyncio.run(root(Request(scope)))
    location = response.headers["location"]
    assert location.startswith("/"), location
    assert location.endswith("?token=s3cret"), f"the redirect dropped the token: {location}"


def test_both_launchers_install_before_they_run_anything():
    """`hermus` on a fresh checkout must set up first, on either platform.

    The Windows wrapper shipped without that branch: on a clean Python the CLI
    import fails on pydantic and the room never opens, so the install has to
    happen in the launcher, before any dependency is imported.
    """
    root = Path(__file__).resolve().parents[1]
    posix = (root / "bin" / "hermus").read_text(encoding="utf-8")
    windows = (root / "hermus.cmd").read_text(encoding="utf-8")
    assert "bootstrap_live.py" in posix and "bootstrap_live.py" in windows, "a launcher lost its visible setup path"
    assert '$#" -eq 0' in posix, "the POSIX launcher no longer special-cases a bare `hermus`"
    assert 'if not exist "%PY%"' in windows, "the Windows launcher no longer installs when the runtime is missing"
    assert "setup.ps1" in windows, "the Windows launcher installs nothing"


def test_the_welcome_flow_keeps_the_launcher_token():
    """A gated install that starts at the welcome screen must arrive authenticated.

    The launcher opens the root with the token; the root sends a machine with no
    usable model to /welcome, and from there the room is a separate navigation. If
    that hop drops the token, the first turn of the first session is a 401.
    """
    root = Path(__file__).resolve().parents[1]
    client = (root / "gateway" / "static" / "welcome.js").read_text(encoding="utf-8")
    assert 'localStorage.setItem("hermus_gateway_token"' in client, "the welcome flow stores no token"
    assert 'localStorage.getItem("hermus_gateway_token")' in client, "the welcome flow reads no token"
    assert client.count("carryToken(") >= 3, "a navigation still hands over a bare URL"
    assert 'window.location.href = "http://' not in client
