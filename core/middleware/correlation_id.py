from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

CORRELATION_ID_HEADER = "X-Correlation-ID"
_UNSET = "-"
_current_correlation_id: ContextVar[str] = ContextVar(
    "correlation_id", default=_UNSET
)


def get_correlation_id() -> str:
    """Return the current request's correlation id (or ``"-"`` if none)."""
    return _current_correlation_id.get()


def set_correlation_id(value: str) -> Token[str]:
    """Bind a correlation id to the current context; returns a reset token."""
    return _current_correlation_id.set(value)


def reset_correlation_id(token: Token[str]) -> None:
    _current_correlation_id.reset(token)


class CorrelationIdMiddleware:
    """Assigns every request a stable correlation id.

    Honors an inbound ``X-Correlation-ID`` header when present; otherwise
    mints a UUID4. The value is bound to a ContextVar so log filters can pull
    it into records, mirrored onto the response header so downstream services
    can forward it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get("HTTP_X_CORRELATION_ID")
        correlation_id = (incoming or uuid.uuid4().hex)[:64]
        request.correlation_id = correlation_id
        token = set_correlation_id(correlation_id)
        try:
            response = self.get_response(request)
            response[CORRELATION_ID_HEADER] = correlation_id
            return response
        finally:
            reset_correlation_id(token)
