from __future__ import annotations


class ShippingError(Exception):
    """Base class for shipping-abstraction errors."""


class ShippingProviderNotFoundError(ShippingError):
    """Raised when a provider code is not registered in the carrier registry."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(
            f"Shipping provider '{code}' is not registered. "
            "Make sure its app is in INSTALLED_APPS and AppConfig.ready() "
            "calls register_provider()."
        )


class ShipmentAwaitingPaymentError(ShippingError):
    """A courier shipment was requested for an order that still owes payment.

    Raised by every carrier's mint choke point while
    ``Order.awaits_online_payment`` is True. A voucher minted for such an
    order ships goods nobody has paid for: an online-paid voucher carries
    no cash-on-delivery amount, so the courier hands the parcel over for
    free, and the mint also advances the order to PROCESSING.

    Deliberately not a carrier API error, so no Celery retry policy
    (``AcsRetryableError`` / ``BoxNowRetryableError``) matches it and no
    "shipment creation failed" alert fires: nothing is broken — the
    payment webhook dispatches the mint once the shopper pays.
    """

    def __init__(self, order_id: int) -> None:
        self.order_id = order_id
        super().__init__(
            f"Order {order_id} is still awaiting its online payment — a "
            "courier shipment is only created once the payment is confirmed."
        )
