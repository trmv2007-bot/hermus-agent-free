"""The install report has to finish on a cp1252 console.

``setup.cmd`` pipes bootstrap through PowerShell, so stdout is a pipe and Python
encodes it with the console code page rather than UTF-8. Printing the report's
status marks then raised ``UnicodeEncodeError`` *after* a successful install — the
last thing a new person saw was a traceback over work that had already been done.
The marks stay in the text; only the write is made tolerant.
"""

from __future__ import annotations

import io
import sys

import pytest

import bootstrap
from core import console_encoding, diagnostics


def _report() -> dict:
    return {
        "platform": {"family": "test"},
        "overall": {"passed": 1, "total": 1, "required": True},
        "subsystems": {
            "dependencies": {
                "name": "Dependencies",
                "status": diagnostics.INSTALLED_VERIFIED,
                "ok": True,
                "detail": "installed",
                "hint": "",
                "required": True,
            }
        },
    }


def _cp1252(buffer: io.BytesIO) -> io.TextIOWrapper:
    return io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")


def test_the_marks_would_raise_on_an_unguarded_cp1252_stream():
    """The defect itself, so the guard below has something to prove."""
    with pytest.raises(UnicodeEncodeError):
        _cp1252(io.BytesIO()).write(diagnostics.INSTALLED_VERIFIED)


def test_the_report_prints_on_a_cp1252_stream(monkeypatch):
    buffer = io.BytesIO()
    stream = _cp1252(buffer)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", _cp1252(io.BytesIO()))

    console_encoding.configure_console()
    bootstrap.print_installation_report(_report())
    stream.flush()

    printed = buffer.getvalue().decode("utf-8", "replace")
    assert diagnostics.INSTALLED_VERIFIED in printed
    assert "HERMUS SETUP: READY" in printed


class _AlreadyWritten(io.TextIOWrapper):
    """A stream that refuses a new encoding, as a wrapper does after a write."""

    def reconfigure(self, **kwargs):  # type: ignore[override]
        if "encoding" in kwargs:
            raise OSError("cannot change encoding after a write")
        super().reconfigure(**kwargs)


def test_a_stream_that_refuses_utf8_still_finishes_the_report(monkeypatch):
    buffer = io.BytesIO()
    stream = _AlreadyWritten(buffer, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", _cp1252(io.BytesIO()))

    console_encoding.configure_console()
    bootstrap.print_installation_report(_report())
    stream.flush()

    printed = buffer.getvalue().decode("cp1252")
    assert "INSTALLED + VERIFIED" in printed
    assert "HERMUS SETUP: READY" in printed


def test_a_stream_that_cannot_reconfigure_is_left_alone(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    console_encoding.configure_console()
    print(diagnostics.INSTALLED_VERIFIED)

    assert diagnostics.INSTALLED_VERIFIED in out.getvalue()


def test_the_installer_configures_the_console_before_it_runs(monkeypatch):
    """`main` must make the console safe before any code can print a status."""
    steps: list[str] = []
    monkeypatch.setattr(console_encoding, "configure_console", lambda: steps.append("console"))
    monkeypatch.setattr(bootstrap, "run", lambda **kwargs: steps.append("run") or 0)

    assert bootstrap.main(["bootstrap"]) == 0
    assert steps == ["console", "run"]
