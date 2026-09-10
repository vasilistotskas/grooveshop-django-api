import asyncio
import json
import logging
import os

from django.db import connection

from core.middleware.correlation_id import get_correlation_id


class TenantContextFilter(logging.Filter):
    """Attach ``schema_name`` + ``domain_url`` to every log record.

    Same field names/semantics as ``django_tenants.log.TenantContextFilter``,
    but defensive: the vendored filter dereferences ``connection.tenant``
    unconditionally (``connection.tenant.schema_name``), which raises
    ``AttributeError`` — crashing the log call itself — whenever no
    tenant is bound (Celery tasks, management commands, app startup;
    ``connection.tenant`` is routinely absent/``None`` outside a
    request handled by ``TenantMainMiddleware``, which is the common
    case in this codebase). Falls back to ``"-"`` there instead.
    """

    def filter(self, record):
        tenant = getattr(connection, "tenant", None)
        record.schema_name = getattr(tenant, "schema_name", None) or "-"
        record.domain_url = getattr(tenant, "domain_url", None) or "-"
        return True


class HostnameFilter(logging.Filter):
    def filter(self, record):
        record.hostname = os.getenv("HOSTNAME", "unknown")
        return True


class CorrelationIdFilter(logging.Filter):
    """Inject the current request's correlation id into log records."""

    def filter(self, record):
        record.correlation_id = get_correlation_id()
        return True


class DropAsyncioCancelledError(logging.Filter):
    """Drop ``asyncio`` log records whose exc_info is ``CancelledError``.

    When a client disconnects mid-request under Django ASGI / asgiref the
    inner future is cancelled and asgiref re-raises ``CancelledError``,
    which asyncio's default ``loop.set_exception_handler`` logs at ERROR
    with a full Django middleware traceback. That is expected behaviour,
    not a bug — the request simply went away — but it produces large,
    misleading tracebacks that look like real 500s in log search.

    This filter only strips ``CancelledError`` records on the ``asyncio``
    logger; any other asyncio errors still pass through.
    """

    def filter(self, record):
        exc = record.exc_info[1] if record.exc_info else None
        return not isinstance(exc, asyncio.CancelledError)


class JsonFormatter(logging.Formatter):
    """Render each record as exactly ONE line of valid JSON.

    Replaces a format STRING that only looked like JSON::

        '{"timestamp": "%(asctime)s", ..., "message": "%(message)s"}'

    ``logging.Formatter`` interpolates that and then, for a record
    carrying ``exc_info``, appends the traceback *after* the closing
    brace. The result is one JSON object followed by N bare text lines,
    so every ``exc_info=True`` call site — there are 17 — emitted an
    event whose traceback the log pipeline could not attach to it, or
    parse at all.

    Interpolation is also the wrong tool for the message field. Nothing
    escapes it, so a message containing a double quote, a backslash or a
    newline produced malformed JSON:

    * ``log.info('Upstream said: {"detail": "not found"}')`` closed the
      string early — and logging upstream error BODIES is a standing
      rule in this codebase, so this was hit routinely.
    * a multi-line message split ONE event across several lines, none of
      them parseable.

    ``json.dumps`` escapes all three correctly and the traceback moves
    INSIDE the object, where it belongs to its event.

    Field names and types are kept identical to the old format so
    existing VictoriaLogs queries keep working: ``line`` stays a number,
    ``process``/``thread`` stay strings. ``exception`` and ``stack`` are
    additive and appear only when the record carries them.
    """

    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = None

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process": str(record.process),
            "thread": str(record.thread),
            # The handler's filters set these. ``getattr`` with a
            # default keeps a record that reaches this formatter by
            # another route from raising inside logging itself, which
            # is how the format string behaved (KeyError -> "--- Logging
            # error ---" on stderr and the event lost entirely).
            "pod": getattr(record, "hostname", "-"),
            "correlation_id": getattr(record, "correlation_id", "-"),
            "schema": getattr(record, "schema_name", "-"),
            "domain": getattr(record, "domain_url", "-"),
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            # Another formatter already rendered it onto the record.
            payload["exception"] = record.exc_text

        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # ``ensure_ascii=False``: Greek copy is logged routinely and
        # \u escapes make it unreadable in log search. ``default=str``
        # is the last line of defence — a non-serialisable value in a
        # message argument must never take down the log call.
        return json.dumps(payload, ensure_ascii=False, default=str)
