"""Foundation: central structured logging (core/log.py)."""

import io
import json
import logging

from core.log import bind_request_id, get_logger, setup_logging


def test_get_logger_returns_hermus_hierarchy():
    assert get_logger("core.agent").name == "hermus.core.agent"
    assert get_logger("hermus.core.agent").name == "hermus.core.agent"
    assert get_logger("hermus").name == "hermus"


def test_setup_logging_is_idempotent_and_respects_level():
    setup_logging(level="WARNING")
    try:
        assert logging.getLogger("hermus").getEffectiveLevel() == logging.WARNING
        setup_logging(level="WARNING")
        assert len(logging.getLogger("hermus").handlers) == 1
    finally:
        setup_logging(level="INFO")


def test_text_format_includes_name_and_message():
    stream = io.StringIO()
    setup_logging(level="INFO", stream=stream)
    try:
        get_logger("test.mod").info("hello world")
        out = stream.getvalue()
        assert "hermus.test.mod" in out
        assert "hello world" in out
        assert "INFO" in out
    finally:
        setup_logging(level="INFO")


def test_json_format_emits_single_line_records():
    stream = io.StringIO()
    setup_logging(level="INFO", fmt="json", stream=stream)
    try:
        get_logger("test.mod").warning("watch out")
        payload = json.loads(stream.getvalue().strip())
        assert payload["level"] == "WARNING"
        assert payload["logger"] == "hermus.test.mod"
        assert payload["msg"] == "watch out"
        assert "ts" in payload
    finally:
        setup_logging(level="INFO")


def test_bind_request_id_renders_on_records():
    stream = io.StringIO()
    setup_logging(level="INFO", stream=stream)
    try:
        with bind_request_id("run_123"):
            get_logger("test.mod").info("correlated")
        out = stream.getvalue()
        assert "req=run_123" in out
    finally:
        setup_logging(level="INFO")


def test_log_level_reads_env(monkeypatch):
    import core.log as logmod

    monkeypatch.setattr(logmod, "_configured", False)
    monkeypatch.setenv("HERMUS_LOG_LEVEL", "ERROR")
    get_logger("env.check")
    try:
        assert logging.getLogger("hermus").getEffectiveLevel() == logging.ERROR
    finally:
        monkeypatch.setattr(logmod, "_configured", False)
        setup_logging(level="INFO")
