from enum import StrEnum


class LandedPayment(StrEnum):
    """What a provider read back holds for an unpaid order
    (``order.payments.settlement.settle_landed_payment``).

    Only ``NONE`` lets the sweep cancel the order. Money the provider took
    is settled or put to the store's review (``SETTLED``); ``UNSETTLED`` is
    a payment still in flight, which its webhook settles.
    """

    #: The provider holds no payment for the order.
    NONE = "none"
    #: The order is paid now, or the money taken waits for the store's
    #: review (``payment_hold.awaits_payment_review``).
    SETTLED = "settled"
    #: A payment still in flight (a Stripe session paid by a delayed
    #: method), or one Stripe took that settled nothing.
    UNSETTLED = "unsettled"
