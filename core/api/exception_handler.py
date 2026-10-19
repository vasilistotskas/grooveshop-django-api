"""The one DRF exception handler: today's error body, plus stable codes.

Every error the API raises keeps the body DRF already gave it (``detail``
for a plain error, the field keys for a validation error) and gains:

``code``
    The top-level error code. A plain error carries its own; a validation
    error carries the one code all its errors share, else ``invalid``.
    Left out when the body already has a key of that name (a ``code``
    field such as a coupon code), where ``fields`` is the only carrier.

``fields``
    Validation errors only: the body's field keys again, each a list of
    ``{message, code}``, nested where the serializer nests. Clients branch
    on ``code`` and show ``message``; they never compare translated text.

A code is a stable snake_case string, never passed through ``_()``; the
message is the translated sentence. A response a view builds itself
(``return Response(..., status=4xx)``) never reaches a handler (DRF), so
it keeps its own shape. Anything that is not an ``APIException`` is left
to Django (a logged 500 with no body of ours), so no exception text can
reach a client through here, and a 5xx ``APIException`` gains ``code``
only.
"""

from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

#: The code of a validation error whose errors do not share one.
INVALID_CODE = ValidationError.default_code

CODE_KEY = "code"
FIELDS_KEY = "fields"


def _leaves(data: Any):
    """Every error message in a (possibly nested) error body."""
    if isinstance(data, dict):
        for value in data.values():
            yield from _leaves(value)
    elif isinstance(data, (list, tuple)):
        for value in data:
            yield from _leaves(value)
    else:
        yield data


def _code_of(leaf: Any, default: str) -> str:
    return leaf.code if isinstance(leaf, ErrorDetail) else default


def _detail(leaf: Any, default: str) -> dict[str, str]:
    return {"message": str(leaf), "code": _code_of(leaf, default)}


def _field_details(value: Any, default: str) -> Any:
    """``{message, code}`` for every message under a field.

    A field always yields a list, even when the raise gave a bare string
    (``ValidationError({"product": "..."})``); nested serializers keep
    their nesting.
    """
    if isinstance(value, dict):
        return {k: _field_details(v, default) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [
            _field_details(v, default)
            if isinstance(v, (dict, list, tuple))
            else _detail(v, default)
            for v in value
        ]
    return [_detail(value, default)]


def _top_level_code(data: Any, default: str) -> str:
    codes = {_code_of(leaf, default) for leaf in _leaves(data)}
    return codes.pop() if len(codes) == 1 else default


def exception_handler(exc: Exception, context: dict[str, Any]):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    data = response.data
    default = getattr(exc, "default_code", INVALID_CODE)

    if isinstance(data, dict):
        # A plain error is DRF's own ``{"detail": ...}``: its one leaf
        # gives the code. Any ``ValidationError`` also gets ``fields``,
        # whatever the keys of its dict, so a client reads one rule.
        body: dict[str, Any] = dict(data)
        if CODE_KEY not in body:
            body[CODE_KEY] = _top_level_code(data, default)
        if (
            isinstance(exc, ValidationError)
            and response.status_code < status.HTTP_500_INTERNAL_SERVER_ERROR
            and FIELDS_KEY not in body
        ):
            body[FIELDS_KEY] = _field_details(data, default)
    else:
        # A bare list body (``ValidationError("...")`` raised outside a
        # serializer) cannot grow keys without changing its type.
        return response

    response.data = body
    return response
