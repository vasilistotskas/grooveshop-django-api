"""Typed exceptions for the myDATA integration.

The distinction between transport / auth / validation / duplicate
errors drives the retry policy in :func:`order.mydata.service.submit_invoice`
and the UI copy in the admin action — so every error path must land
on one of these classes.

=============================  =========================
Class                          Retryable?
=============================  =========================
MyDataTransportError           yes (network, 5xx, 429)
MyDataAuthError                no (rotate creds)
MyDataValidationError          no (fix master data)
MyDataDuplicateError           no (recover via query)
=============================  =========================
"""

from __future__ import annotations


class MyDataError(Exception):
    """Base class for every error raised by the myDATA integration."""

    code: str = ""
    message: str = ""

    def __init__(self, message: str = "", *, code: str = ""):
        self.message = message or self.__class__.__name__
        self.code = code
        super().__init__(self.message)


class MyDataTransportError(MyDataError):
    """Transient transport-level failure — network error, timeout,
    5xx, or 429 rate-limit. The Celery task retries these with
    exponential backoff using the same ``uid`` (AADE dedupes)."""


class MyDataAuthError(MyDataError):
    """Authentication / authorization failure — ``aade-user-id`` or
    ``Ocp-Apim-Subscription-Key`` rejected, or subscription not
    approved. Retrying will not help; rotate credentials."""


class MyDataValidationError(MyDataError):
    """Terminal schema / data validation error from AADE. Invoice is
    marked ``REJECTED``; operator must correct master data (seller VAT,
    buyer VAT, VAT category, rounding, etc.) and regenerate."""


class MyDataDuplicateError(MyDataError):
    """AADE error ``228`` — the ``uid`` was already registered under
    another MARK. The integration recovers by querying
    ``RequestTransmittedDocs`` and writing the existing MARK back to
    the invoice — it is not a user-facing error."""


class MyDataInactiveCounterpartError(MyDataValidationError):
    """AADE error ``102`` — the buyer VAT number isn't registered, is
    registered as inactive, or belongs to a business with closed
    fiscal presence. Distinct from generic validation errors because
    it has a customer-visible remediation path (ask them to
    double-check their ΑΦΜ) rather than an ops-data fix.

    Subclass of ``MyDataValidationError`` so existing ``except
    MyDataValidationError`` handlers still catch it — callers that
    want the distinct UX branch check for the specific subclass
    first."""
