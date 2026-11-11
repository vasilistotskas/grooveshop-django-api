"""Exception hierarchy for the ACS REST API client.

Mirrors the BoxNow split (config / API / auth / retryable) so order
flow code can target a single common pattern.

ACS error semantics (per PDF):

* HTTP 200 + ``ACSExecution_HasError = false``  → success.
* HTTP 200 + ``ACSExecution_HasError = true``   → business error;
  message in ``ACSExecutionErrorMessage``.
* HTTP 403 / 406                                → auth-layer rejection;
  ACS answers 406 to overlapping calls from one account (bounded by
  ``shipping_acs.concurrency``), so Celery retries it. A *persistent*
  403/406 means a wrong AcsApiKey or a de-listed calling IP.
* HTTP 5xx, a lost answer, a non-JSON 200       → outcome unknown
  (:class:`AcsOutcomeUnknownError`): a read is retried, a voucher
  request is never sent again.
* Other 4xx                                     → not ACS's documented
  answer (:class:`AcsUndocumentedHttpError`): nothing was made, and
  nothing was said about the request; surface to admin.
"""

from __future__ import annotations

from shipping.exceptions import CarrierError, CarrierUnavailableError


class AcsError(CarrierError):
    """Base class for ACS-related errors."""


class AcsConfigError(AcsError, CarrierUnavailableError):
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


class AcsUndocumentedHttpError(AcsAPIError):
    """An HTTP 4xx that is not in ACS's envelope and is not 403/406.

    The manual has the server answer 200, 403 or 406 (April 2026 edition,
    p. 1-2), so a 400, 401, 404 or 429 is a proxy's or a gateway's, or an
    answer ACS does not document. A known outcome, like every 4xx: no
    voucher was made (#626). But it says nothing about what was asked,
    so it is not ACS refusing the order: staff are not told to fix an
    address, and while a voucher hold is in force an order whose request
    was held stays held (``shipping_acs.voucher_hold``).
    """


class AcsRetryableError(AcsAPIError, CarrierUnavailableError):
    """Raised when a retry may succeed: a connection never established,
    a full request cap, an auth-layer rejection, and (as the subclass
    :class:`AcsOutcomeUnknownError`) every failure after the request may
    have reached ACS.

    Celery autoretries.
    """


class AcsOutcomeUnknownError(AcsRetryableError):
    """The request may have been executed at ACS, but its answer is lost.

    A read timeout, a connection dropped after the request was sent
    (urllib3's ``ProtocolError``, which ``requests`` re-raises as a plain
    ``ConnectionError``), any other transport failure not proven to have
    happened before the connection was made, an HTTP 5xx, or a 200 whose
    body is not JSON. The manual says the server answers 200, 403 or 406
    and says nothing of the rest (April 2026 edition, p. 1-2).

    Retrying a read is harmless, so this stays retryable. A write that is
    not idempotent must not be re-sent: ``ACS_Create_Voucher`` has no
    idempotency key, and ``AcsService.create_voucher_for_order`` turns
    this error into a voucher request of unknown outcome instead.
    """


class AcsVoucherRequestNotSentError(AcsRetryableError):
    """The voucher request was stopped at its request slot, before the
    POST: nothing reached ACS.

    Raised as itself when the store's voucher hold could not be read
    (Celery retries), and as :class:`AcsVoucherRequestHeldError` when a
    hold is in force. Not a call to ACS, so ``AcsClient._call`` writes no
    ``AcsApiCall`` row and no call log line for it.
    """


class AcsVoucherRequestHeldError(AcsVoucherRequestNotSentError):
    """The voucher request was not sent: an ``AcsVoucherHold`` is in force.

    ACS just left a voucher request of this store with an unknown outcome,
    so no further one is sent until ACS answers again
    (``shipping_acs.voucher_hold``). Nothing reached ACS, so this is a
    known outcome and the request is sent later, by
    ``shipping_acs.tasks.issue_held_acs_vouchers``; it never becomes a
    voucher request of unknown outcome.
    """


class AcsVoucherRequestUnknownError(AcsError):
    """The shipment's voucher request has an unknown outcome.

    Raised by ``AcsService.create_voucher_for_order`` when it records the
    state (``AcsShipment.voucher_request_unknown_at``) and every time a
    caller asks for a voucher while it holds. Not retryable: only staff
    end it, by adopting the voucher ACS holds or by requesting again.
    """


class AcsVoucherResolutionError(AcsError):
    """Staff cannot resolve a voucher check this way.

    Raised by the adopt / request-again / delete / mark-checked services
    with a message an operator can act on (the admin shows it verbatim).
    """


class AcsUnprintedVouchersError(AcsAPIError):
    """``ACS_Issue_Pickup_List`` refused the day because labels are unprinted.

    ACS refuses the whole pickup list while any voucher for the date is
    unprinted (``Unprinted_Found > 0``; the voucher numbers arrive in
    ``raw["Unprinted_Vouchers"]``). An expected business outcome with a
    single fix — print the named labels, then issue again — so the daily
    task reports it as a status instead of failing, while every other
    refusal stays a plain :class:`AcsAPIError`. Not retryable: nothing
    changes until someone prints.
    """


class AcsAuthError(AcsRetryableError):
    """Raised on HTTP 403 / 406 — auth-layer rejection.

    Subclasses :class:`AcsRetryableError` because production ACS
    returns 406s that succeed on the next attempt once fewer calls are
    in flight (same key/IP succeeded for sibling vouchers in the same
    second). An auth-rejected request was never
    processed by ACS, so retrying is safe even for non-idempotent
    aliases like ``ACS_Create_Voucher``. When the key/IP is genuinely
    wrong, Celery's capped retries exhaust and the task fails loudly
    instead of silently returning.
    """


class AcsCodSettlementError(AcsError):
    """A COD parcel cannot be recorded as received outside ACS.

    Raised by ``AcsService.record_cod_received_outside_acs`` with a
    message an operator can act on (the admin action shows it verbatim).
    """


class AcsPayoutReconciliationError(AcsError):
    """A COD payout row cannot be marked as reconciled.

    Raised by ``AcsService.reconcile_cod_payout`` with a message an
    operator can act on (the admin action shows it verbatim).
    """
