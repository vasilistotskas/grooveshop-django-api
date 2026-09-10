from __future__ import annotations

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class BoxNowError(Exception):
    """Base class for all BoxNow-related errors."""


class BoxNowConfigError(BoxNowError):
    """Raised when required BoxNow credentials or settings are missing."""


class BoxNowUnsupportedSettlementError(BoxNowError):
    """A pay-way BoxNow cannot settle reached shipment creation.

    Today that means ``COURIER_CASH``: a locker has no POS and takes no
    cash, so a courier-COD pay-way can never be collected there.

    This is a "should be unreachable" guard, and it is deliberately
    loud. ``BoxNowCarrier.supported_settlements`` removes such pay-ways
    from the checkout, so arriving here means something bypassed that
    gate — a hand-built order, a direct service call, or a regression
    in the pay-way filter. The predecessor of this code had no guard at
    all and silently minted the wrong commercial product, charging a
    cash-handling surcharge for a courier who was never involved.
    Failing here strands one shipment and raises an ops alert, which is
    strictly better than mis-charging a customer.

    Attributes:
        order_id:   The order whose shipment could not be created.
        settlement: The offending ``PaySettlement`` value.
    """

    def __init__(self, *, order_id: int, settlement: str) -> None:
        self.order_id = order_id
        self.settlement = settlement
        super().__init__(
            f"BoxNow cannot settle pay-way settlement {settlement!r} "
            f"(order {order_id}): nobody meets the shopper at a locker, "
            f"so no cash can be collected. "
            f"Expected 'carrier_terminal' (PAY ON THE GO), 'online', "
            f"or 'offline_transfer'."
        )


class BoxNowAPIError(BoxNowError):
    """
    Raised for non-2xx HTTP responses from the BoxNow API.

    Attributes:
        status_code: HTTP status code returned by BoxNow.
        code:        BoxNow application error code (e.g. "P410"), or None.
        message:     Human-readable error message from BoxNow.
        details:     Additional structured error detail dict, or None.
        response_text: Raw response body text for debugging.
    """

    def __init__(
        self,
        status_code: int,
        code: str | None = None,
        message: str = "",
        details: dict | None = None,
        *,
        response_text: str = "",
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.response_text = response_text
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"BoxNow API {self.status_code} [{self.code}]: {self.message}"


class BoxNowAuthError(BoxNowAPIError):
    """
    Raised for HTTP 401 / 403 responses from BoxNow.

    Indicates that the access token is expired, invalid, or the account
    has been disabled.
    """


class BoxNowRetryableError(BoxNowAPIError):
    """
    Raised for transient failures that Celery tasks should auto-retry on.

    Covers HTTP 5xx responses and connection-level errors (wrapped in this
    class so Celery's ``autoretry_for`` can target a single type).
    """
