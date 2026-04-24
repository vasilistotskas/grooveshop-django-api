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
