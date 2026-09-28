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


class ShippingUnavailableError(ShippingError):
    """No active ``ShippingRate`` matches (provider, kind, country).

    Raised by :meth:`shipping.services.ShippingService.active_rate` —
    the single gate every quote and every order-creation path goes
    through. Covers every reason a combination can be unavailable: the
    provider is inactive or unconfigured, the kind is disabled, or
    simply no rate row exists for that country (the common Cyprus
    case: a rate must be added before the country becomes orderable
    for that carrier).
    """

    def __init__(
        self,
        *,
        provider_code: str | None,
        kind: str,
        country_code: str | None,
    ) -> None:
        self.provider_code = provider_code
        self.kind = kind
        self.country_code = country_code
        super().__init__(
            f"No active shipping rate for provider={provider_code!r} "
            f"kind={kind!r} country={country_code!r}."
        )


class ShippingWeightExceededError(ShippingError):
    """The cart's weight exceeds the resolved rate's ``max_weight_grams``.

    Raised by :meth:`shipping.services.ShippingService.assert_available`
    once a rate has been found but the parcel is too heavy for it —
    e.g. a Cyprus BoxNow rate capped at 4 kg.
    """

    def __init__(self, *, weight_grams: int, max_weight_grams: int) -> None:
        self.weight_grams = weight_grams
        self.max_weight_grams = max_weight_grams
        super().__init__(
            f"Cart weight {weight_grams}g exceeds the "
            f"{max_weight_grams}g cap for this shipping option."
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
