"""Every production log line must be ONE parseable JSON object.

The production ``json`` formatter used to be a format STRING shaped
like JSON. ``logging.Formatter`` interpolates it and then appends the
traceback *after* the closing brace, and nothing escapes the message —
so three shapes broke the log pipeline:

* ``exc_info=True`` (17 call sites) emitted the object plus N bare
  traceback lines, none of them JSON;
* a message containing a double quote closed the string early, which
  this codebase hits routinely because logging upstream error BODIES is
  a standing rule;
* a multi-line message split one event across several unparseable
  lines.

Each test below fails against the format string and passes against
``JsonFormatter``. The field names and types are asserted too — they are
a contract with the VictoriaLogs queries in the runbooks.
"""

from __future__ import annotations

import json
import logging

import pytest

from core.logging import JsonFormatter

CONTEXT = {
    "hostname": "backend-abc123",
    "correlation_id": "cid-1",
    "schema_name": "webside",
    "domain_url": "webside.gr",
}


def _record(msg: str, *args, **kwargs) -> logging.LogRecord:
    record = logging.LogRecord(
        name="order.services",
        level=logging.ERROR,
        pathname=__file__,
        lineno=42,
        msg=msg,
        args=args,
        exc_info=kwargs.get("exc_info"),
        func="release_reservations",
    )
    for key, value in CONTEXT.items():
        setattr(record, key, value)
    return record


def _emit(record: logging.LogRecord) -> str:
    return JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S").format(record)


class TestOneLineOneObject:
    def test_a_traceback_stays_inside_the_object(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = _record("release failed", exc_info=sys.exc_info())

        line = _emit(record)

        assert "\n" not in line, "a record must never span lines"
        payload = json.loads(line)
        assert payload["message"] == "release failed"
        assert "ValueError: boom" in payload["exception"]
        assert "Traceback" in payload["exception"]

    def test_a_quoted_message_stays_parseable(self):
        """The `forwardUpstreamClientError` shape: a JSON error body
        logged verbatim. The format string closed the string early
        here and produced invalid JSON."""
        body = '{"detail": "not found"}'
        payload = json.loads(_emit(_record("Upstream said: %s", body)))

        assert payload["message"] == f"Upstream said: {body}"

    @pytest.mark.parametrize(
        "raw",
        [
            "line one\nline two",
            r"path C:\Users\x",
            'quote " and backslash \\',
            "tab\tseparated",
        ],
    )
    def test_control_characters_and_escapes_survive(self, raw):
        line = _emit(_record("%s", raw))

        assert "\n" not in line
        assert json.loads(line)["message"] == raw

    def test_greek_is_not_escaped_into_unreadability(self):
        """``ensure_ascii=False`` — Greek copy is logged routinely and
        \\u escapes make log search useless."""
        greek = "Πληρωμή με κάρτα"
        line = _emit(_record("%s", greek))

        assert greek in line
        assert json.loads(line)["message"] == greek


class TestFieldContract:
    def test_field_names_and_types_are_unchanged(self):
        """VictoriaLogs queries in the runbooks select on these."""
        payload = json.loads(_emit(_record("hello")))

        assert payload["level"] == "ERROR"
        assert payload["logger"] == "order.services"
        assert payload["function"] == "release_reservations"
        assert payload["pod"] == "backend-abc123"
        assert payload["correlation_id"] == "cid-1"
        assert payload["schema"] == "webside"
        assert payload["domain"] == "webside.gr"
        # ``line`` was unquoted in the old format string, the other two
        # were quoted. Keep both, or existing numeric filters break.
        assert payload["line"] == 42
        assert isinstance(payload["process"], str)
        assert isinstance(payload["thread"], str)

    def test_optional_fields_are_absent_when_unset(self):
        payload = json.loads(_emit(_record("hello")))

        assert "exception" not in payload
        assert "stack" not in payload

    def test_a_record_without_the_filters_does_not_crash(self):
        """The filters live on the handler. A record arriving by another
        route must still log — the format string raised KeyError, which
        loses the event entirely."""
        record = logging.LogRecord(
            name="x",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="no context",
            args=(),
            exc_info=None,
        )

        payload = json.loads(_emit(record))

        assert payload["message"] == "no context"
        assert payload["schema"] == "-"
        assert payload["correlation_id"] == "-"

    def test_an_unserialisable_argument_cannot_break_logging(self):
        class Opaque:
            def __str__(self):
                return "<opaque>"

        payload = json.loads(_emit(_record("got %s", Opaque())))

        assert payload["message"] == "got <opaque>"


class TestProductionWiring:
    """The production ``json`` formatter is only *configured* on the
    ``IS_KUBERNETES`` branch, so asserting against ``settings.LOGGING``
    would skip everywhere the tests actually run — CI included — and
    guard nothing. Read the settings source instead: that is true in
    every environment, and it is the reintroduction of a JSON-shaped
    format string that has to be caught."""

    @staticmethod
    def _settings_source() -> str:
        from pathlib import Path

        import settings

        return Path(settings.__file__).read_text(encoding="utf-8")

    def test_the_json_formatter_is_the_class(self):
        assert '"()": "core.logging.JsonFormatter"' in self._settings_source()

    def test_no_formatter_hand_rolls_json_with_a_format_string(self):
        """The exact shape that broke: a ``format`` string opening with
        a brace. Interpolation cannot escape a message, so any such
        string is malformed the first time one contains a quote."""
        source = self._settings_source()

        assert '"format": \'{"' not in source
        assert '"format": "{\\"' not in source
        assert '%(message)s"}' not in source
