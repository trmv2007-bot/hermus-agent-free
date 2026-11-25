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
