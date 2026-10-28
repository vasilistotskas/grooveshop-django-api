"""What every ACS call leaves behind: one log line and one history row.

``AcsClient._call`` is the single chokepoint, so :func:`observe_call` is
the only place that decides what is logged, what is persisted and what is
kept out of both. Observing a call must never change its result: the
persisted write is isolated in a savepoint and every failure of it is
logged and swallowed.

The log line is one INFO/WARNING/ERROR record whose queryable fields ride
in ``extra={"fields": {...}}`` (``core.logging.JsonFormatter`` merges them
into the JSON object): ``acs_alias``, ``outcome``, ``duration_ms``,
``http_status``, ``error_message`` and the business identifiers present in
the call (``voucher_no``, ``pickup_date``, ``pickup_list_no``,
``reference_no``, ``order_id``). The tenant schema is the formatter's own
``schema`` field.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db import transaction

from shipping_acs.exceptions import (
    AcsAPIError,
    AcsAuthError,
    AcsRetryableError,
)
from shipping_acs.models.api_call import AcsApiCall, AcsApiCallOutcome

logger = logging.getLogger("shipping_acs.client")

REDACTED = "[redacted]"

# Compared after lower-casing and dropping every non-alphanumeric, so
# ``Company_ID``, ``company-id`` and ``companyId`` are one key.
_SENSITIVE_KEYS = frozenset(
    {
        "companyid",
        "companypassword",
        "userid",
        "userpassword",
        "acsapikey",
        "apikey",
        "password",
    }
)

# A string this long is a PDF/base64 body, never a business value.
_BULK_STRING_CHARS = 2000
_REQUEST_CAP_BYTES = 8 * 1024
_RESPONSE_CAP_BYTES = 16 * 1024


@dataclass
class Exchange:
    """What the wire gave back, filled in by ``AcsClient._exchange``."""

    http_status: int | None = None
    body: Any = None


def _is_sensitive(key: object) -> bool:
    normalised = "".join(ch for ch in str(key).lower() if ch.isalnum())
    return normalised in _SENSITIVE_KEYS


def redact(value: Any) -> Any:
    """``value`` without credentials and without PDF/base64 bodies."""
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    if isinstance(value, str) and len(value) > _BULK_STRING_CHARS:
        return f"<{len(value)} chars omitted>"
    return value


def cap(value: Any, limit: int) -> Any:
    """``value`` when it serialises within ``limit`` bytes, else a stub."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError, ValueError:
        return {"_unserialisable": True}
    size = len(text.encode())
    if size <= limit:
        return value
    return {"_truncated": True, "_size_bytes": size, "preview": text[:1000]}


def _first(params: dict[str, Any], *names: str) -> str:
    wanted = {name.lower() for name in names}
    for key, value in params.items():
        if str(key).lower() in wanted and value not in (None, ""):
            return str(value)
    return ""


def identifiers(params: dict[str, Any]) -> dict[str, Any]:
    """The business identifiers a call carries, omitting absent ones."""
    found: dict[str, Any] = {
        "voucher_no": _first(params, "Voucher_No", "Main_Voucher_No"),
        "pickup_date": _first(params, "Pickup_Date"),
        "pickup_list_no": _first(params, "PickupList_No", "Mass_Number"),
        "reference_no": _first(params, "reference_no"),
    }
    order_ref = _first(params, "Reference_Key1")
    if order_ref.isdigit():
        found["order_id"] = int(order_ref)
    return {key: value for key, value in found.items() if value}


def _value_row_message(body: Any) -> str:
    """``Error_Message`` of the first ``ACSValueOutput`` row, if any."""
    if not isinstance(body, dict):
        return ""
    envelope = body.get("ACSOutputResponce") or body.get("ACSOutputResponse")
    rows = (envelope or {}).get("ACSValueOutput") or []
    if not rows or not isinstance(rows[0], dict):
        return ""
    return str(rows[0].get("Error_Message") or "").strip()


def _classify(
    exc: BaseException | None, exchange: Exchange, value_message: str
) -> tuple[str, str, int]:
    """``(outcome, error_message, log level)``."""
    if exc is None:
        if value_message:
            return (
                AcsApiCallOutcome.BUSINESS_ERROR,
                value_message,
                logging.WARNING,
            )
        return AcsApiCallOutcome.OK, "", logging.INFO
    if isinstance(exc, AcsAuthError):
        return AcsApiCallOutcome.AUTH, exc.error_message, logging.WARNING
    if isinstance(exc, AcsRetryableError):
        outcome = (
            AcsApiCallOutcome.RETRYABLE
            if exc.http_status is not None
            else AcsApiCallOutcome.TRANSPORT
        )
        return outcome, exc.error_message, logging.WARNING
    if isinstance(exc, AcsAPIError):
        # ``raw`` is set only for ACSExecution_HasError: ACS refusing in
        # its own words. A bare 4xx or a non-JSON body is unexpected.
        if exc.raw:
            return (
                AcsApiCallOutcome.BUSINESS_ERROR,
                exc.error_message,
                logging.WARNING,
            )
        return AcsApiCallOutcome.UNEXPECTED, exc.error_message, logging.ERROR
    return AcsApiCallOutcome.UNEXPECTED, str(exc), logging.ERROR


def _pickup_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def observe_call(
    *,
    alias: str,
    params: dict[str, Any] | None,
    started: float,
    exchange: Exchange,
    exc: BaseException | None = None,
) -> None:
    """Log and persist one finished call. Never raises."""
    try:
        _observe(alias, params or {}, started, exchange, exc)
    except Exception:
        logger.warning(
            "ACS call observation failed for %s", alias, exc_info=True
        )


def _observe(
    alias: str,
    params: dict[str, Any],
    started: float,
    exchange: Exchange,
    exc: BaseException | None,
) -> None:
    duration_ms = int((time.monotonic() - started) * 1000)
    http_status = exchange.http_status
    if http_status is None and isinstance(exc, AcsAPIError):
        http_status = exc.http_status
    outcome, error_message, level = _classify(
        exc, exchange, _value_row_message(exchange.body)
    )
    ids = identifiers(params)

    logger.log(
        level,
        "ACS call %s outcome=%s http_status=%s duration_ms=%s",
        alias,
        outcome,
        http_status,
        duration_ms,
        extra={
            "fields": {
                "acs_alias": alias,
                "outcome": str(outcome),
                "duration_ms": duration_ms,
                "http_status": http_status,
                "error_message": error_message,
                **ids,
            }
        },
    )

    body = exchange.body
    if body is None and isinstance(exc, AcsAPIError) and exc.raw:
        body = exc.raw
    _persist(
        alias=alias,
        duration_ms=duration_ms,
        http_status=http_status,
        outcome=outcome,
        error_message=error_message,
        request_params=cap(redact(params), _REQUEST_CAP_BYTES),
        response=cap(redact(body), _RESPONSE_CAP_BYTES),
        ids=ids,
    )


def _persist(
    *,
    alias: str,
    duration_ms: int,
    http_status: int | None,
    outcome: str,
    error_message: str,
    request_params: Any,
    response: Any,
    ids: dict[str, Any],
) -> None:
    try:
        # A savepoint: a failed INSERT must not poison a caller's open
        # transaction, and the call it describes has already happened.
        with transaction.atomic():
            AcsApiCall.objects.create(
                alias=alias,
                duration_ms=duration_ms,
                http_status=http_status,
                outcome=outcome,
                error_message=error_message,
                request_params=request_params,
                response=response,
                voucher_no=ids.get("voucher_no", ""),
                order_id=ids.get("order_id"),
                pickup_date=_pickup_date(ids.get("pickup_date", "")),
                reference_no=ids.get("reference_no", ""),
            )
    except Exception:
        logger.warning(
            "Could not record the ACS call history row for %s",
            alias,
            exc_info=True,
        )
