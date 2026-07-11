"""Exception hierarchy for the ACS REST API client.

Mirrors the BoxNow split (config / API / auth / retryable) so order
flow code can target a single common pattern.

ACS error semantics (per PDF):

* HTTP 200 + ``ACSExecution_HasError = false``  → success.
* HTTP 200 + ``ACSExecution_HasError = true``   → business error;
  message in ``ACSExecutionErrorMessage``.
* HTTP 403 / 406                                → auth-layer rejection;
  transient in practice (~2% of prod tracking polls, self-heals on
  retry — verified 2026-07-11), so Celery retries it. A *persistent*
  403/406 means a wrong AcsApiKey or a de-listed calling IP.
* HTTP 5xx                                      → transient; Celery retries.
* Other 4xx                                     → permanent; surface to admin.
"""

from __future__ import annotations

# Common ACS business-error messages we have seen in the wild.  The
# wire returns plain Greek strings in ``ACSExecutionErrorMessage``;
# this map is for admin debugging hints only — never compared against
# the raw value at runtime.
ACS_ERROR_HINTS: dict[str, str] = {
    "voucher_already_in_pickup_list": (
        "Voucher cannot be deleted because it has already been issued in"
        " a pickup list. Issue a new voucher and contact ACS support."
    ),
    "duplicate_voucher": (
        "Order number conflict — a voucher already exists for this order."
        " Check AcsShipment.voucher_no in admin."
    ),
    "invalid_billing_code": (
        "Billing_Code is not recognised by ACS. Verify settings.ACS_BILLING_CODE"
        " (Greek characters are case-sensitive)."
    ),
}


class AcsError(Exception):
    """Base class for ACS-related errors."""


class AcsConfigError(AcsError):
    """Raised when required ACS credentials or settings are missing."""


class AcsAPIError(AcsError):
    """Raised when ACS reports a business error.

    Attributes:
        alias: The ACSAlias of the failing call (e.g. ``ACS_Create_Voucher``).
        error_message: Verbatim ``ACSExecutionErrorMessage`` from ACS.
        http_status: Underlying HTTP status (only set for non-200 cases).
        raw: Full response body for debugging.
    """

    def __init__(
        self,
        *,
        alias: str,
        error_message: str = "",
        http_status: int | None = None,
        raw: dict | None = None,
    ) -> None:
        self.alias = alias
        self.error_message = error_message
        self.http_status = http_status
        self.raw = raw or {}
        super().__init__(str(self))

    def __str__(self) -> str:
        if self.http_status is not None:
            return (
                f"ACS [{self.alias}] HTTP {self.http_status}: "
                f"{self.error_message}"
            )
        return f"ACS [{self.alias}]: {self.error_message}"


class AcsRetryableError(AcsAPIError):
    """Raised on HTTP 5xx and connection errors — Celery autoretries."""


class AcsAuthError(AcsRetryableError):
    """Raised on HTTP 403 / 406 — auth-layer rejection.

    Subclasses :class:`AcsRetryableError` because production ACS
    returns sporadic 406s that succeed on the next attempt (observed
    on ~2% of tracking polls; same key/IP succeeded for sibling
    vouchers in the same second). An auth-rejected request was never
    processed by ACS, so retrying is safe even for non-idempotent
    aliases like ``ACS_Create_Voucher``. When the key/IP is genuinely
    wrong, Celery's capped retries exhaust and the task fails loudly
    instead of silently returning.
    """
