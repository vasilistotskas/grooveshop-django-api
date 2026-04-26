import asyncio
import os
import logging

from core.middleware.correlation_id import get_correlation_id


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
