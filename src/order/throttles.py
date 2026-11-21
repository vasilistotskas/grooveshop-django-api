"""The order app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    GatewayAnonThrottle,
    GatewayUserRateThrottle,
    UserOrIpRateThrottle,
)


class PaymentAttemptThrottle(GatewayUserRateThrottle):
    scope = "payment"
    fail_closed = True
    gateway_names_order = True


class PaymentAttemptAnonThrottle(GatewayAnonThrottle):
    """Starting a payment, keyed per order when the agent gateway asks for
    the link it needs right after placing an online order (the order the
    route authorizes, never a cart: ``GatewayIdentMixin``)."""

    scope = "payment_anon"
    fail_closed = True
    gateway_names_order = True


class OrderCreateThrottle(GatewayUserRateThrottle):
    scope = "order_create"
    fail_closed = True


class OrderCreateAnonThrottle(GatewayAnonThrottle):
    """Anonymous checkout is a stock- and money-moving endpoint.

    Creating an order reserves or decrements stock, can mint a courier
    voucher and can open a provider payment session, all before anyone
    has authenticated. The global anon budget is a day-scale ceiling and
    does not bound a burst. An order the agent gateway places is
    budgeted per cart (``GatewayAnonThrottle``), like its cart writes.
    """

    scope = "order_create_anon"
    fail_closed = True


class VivaReturnThrottle(UserOrIpRateThrottle):
    """Per-caller throttle for the anonymous Viva hosted-checkout return
    resolver. The global anon limit (100k/day) is far too loose for an
    AllowAny lookup that echoes order id/uuid/status — cap it tightly."""

    scope = "viva_return"
    fail_closed = True


class OrderSpecimenThrottle(UserOrIpRateThrottle):
    """Per-caller throttle for the demo store's specimen receipt, which
    renders a PDF with WeasyPrint on the request worker for every call:
    CPU-heavy, and the shared demo logins make the caller an easy
    amplifier."""

    scope = "order_specimen"
    fail_closed = True
