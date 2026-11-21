from enum import StrEnum


class LandedPayment(StrEnum):
    """What a provider read back holds for an unpaid order
    (``order.payments.settlement.settle_landed_payment``).

    Only ``NONE`` lets the sweep cancel the order: a payment the provider
    holds that the order does not show settled (``UNSETTLED``) means the
    money may have moved, so the order waits for its webhook or a person.
    """

    #: The provider holds no payment for the order.
    NONE = "none"
    #: The order is paid now, or its charge waits for the store's review
    #: (``payment_hold.awaits_payment_review``).
    SETTLED = "settled"
    #: The provider holds a payment, or one in flight, that settled nothing.
    UNSETTLED = "unsettled"
