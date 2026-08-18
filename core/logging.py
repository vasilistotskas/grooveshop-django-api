import asyncio
import os
import logging

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
