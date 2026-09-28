"""Structured logging must stay machine-parseable whatever callers pass."""

from __future__ import annotations

import json
import logging

from andon.logging_setup import JsonFormatter, TextFormatter


def record(msg: str = "hello", **extra) -> logging.LogRecord:
    """Build a record whose ``extra`` fields may use any name, including ones
    that collide with the log envelope."""
    made = logging.LogRecord("andon.test", logging.INFO, __file__, 10, msg, (), None)
    made.__dict__.update(extra)
    return made


class TestJsonFormatter:
    def test_emits_a_single_json_object(self):
        payload = json.loads(JsonFormatter().format(record()))

        assert payload["level"] == "INFO"
        assert payload["logger"] == "andon.test"
        assert payload["msg"] == "hello"
        assert payload["ts"].endswith("+00:00")

    def test_extras_are_promoted_to_top_level_fields(self):
        payload = json.loads(JsonFormatter().format(record(provider="open-meteo", latency_ms=42)))

        assert payload["provider"] == "open-meteo"
        assert payload["latency_ms"] == 42

    def test_an_extra_cannot_overwrite_the_log_envelope(self):
        # "level" is a natural field name in this domain (severity levels), so
        # the formatter must not let it shadow the log level.
        payload = json.loads(JsonFormatter().format(record(level="medium", msg="situation served")))

        assert payload["level"] == "INFO"
        assert payload["extra_level"] == "medium"

    def test_non_serializable_values_do_not_raise(self):
        payload = json.loads(JsonFormatter().format(record(obj=object())))
        assert isinstance(payload["obj"], str)

    def test_exceptions_are_captured(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            made = record()
            made.exc_info = sys.exc_info()
            payload = json.loads(JsonFormatter().format(made))

        assert "ValueError: boom" in payload["exc"]


class TestTextFormatter:
    def test_includes_message_and_extras(self):
        line = TextFormatter().format(record(provider="tomtom"))

        assert "hello" in line
        assert "provider=tomtom" in line
