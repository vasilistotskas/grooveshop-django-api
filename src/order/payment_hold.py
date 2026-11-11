"""How long an unpaid online order may hold its stock.

An online-payment order reserves stock the moment it is created and keeps
it until it is paid or canceled. The hold must end exactly when the payment
can no longer succeed, never before: a payment that lands after the stock
was released books money for goods nobody reserved
(``OrderService.record_payment_after_cancel``). So the window is handed to
the provider as the expiry of the payment link or session
(``expires_at``), the provider reports the expiry it actually applied, and
``Order.payment_hold_expires_at`` stores it. The stuck-order sweep
(``order.tasks.auto_cancel_stuck_pending_orders``) cancels an order once
that instant plus a grace window has passed.

Until a payment is opened, the hold runs from the order's creation.
"""

from datetime import datetime, timedelta

from django.db.models import DateTimeField, ExpressionWrapper, F, QuerySet
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.store_settings import store_setting
from order.enum.status import OrderStatus, PaymentStatus
from order.models.order import Order
from order.payment import StripePaymentProvider, VivaWalletPaymentProvider
from pay_way.enum.settlement import PaySettlement

HOLD_SETTING = "ORDER_ONLINE_PAYMENT_HOLD_MINUTES"
MAX_HOLD_SETTING = "ORDER_ONLINE_PAYMENT_MAX_HOLD_MINUTES"
GRACE_SETTING = "ORDER_ONLINE_PAYMENT_GRACE_MINUTES"
ALERT_SETTING = "ORDER_UNPAID_ONLINE_ALERT_THRESHOLD"

#: Message of the line logged when the unpaid online orders pile up; the
#: ``GrooveShopUnpaidOnlineOrdersSpike`` alert in grooveshop-infrastructure
#: matches it exactly.
SPIKE_MESSAGE = "orders:unpaid_online_spike"


def _fits_providers(minutes: object) -> bool:
    try:
        window = timedelta(minutes=int(str(minutes)))
    except ValueError:
        return False
    return (
        StripePaymentProvider.MIN_SESSION_LIFETIME
        <= window
        <= min(
            StripePaymentProvider.MAX_SESSION_LIFETIME,
            VivaWalletPaymentProvider.MAX_PAYMENT_TIMEOUT,
        )
    )


def validate_hold_minutes_setting(value: object) -> bool:
    """django-extra-settings validator for ``ORDER_ONLINE_PAYMENT_HOLD_MINUTES``.

    The window must fit both providers: no shorter than Stripe's minimum
    session and no longer than Viva's largest timeout. A value outside it
    would give one provider an expiry it refuses or silently clamps, and
    the stock would outlive (or be released before) the payment link.
    """
    return _fits_providers(value)


def validate_max_hold_minutes_setting(value: object) -> bool:
    """Validator for ``ORDER_ONLINE_PAYMENT_MAX_HOLD_MINUTES``.

    The total a new payment link may stretch an order's hold to. It must
    fit the providers like the hold itself and be no shorter than it.
    """
    return (
        _fits_providers(value)
        and timedelta(minutes=int(str(value))) >= hold_window()
    )


def hold_window() -> timedelta:
    return timedelta(minutes=int(store_setting(HOLD_SETTING)))


def grace_window() -> timedelta:
    return timedelta(minutes=int(store_setting(GRACE_SETTING)))


def max_hold_window() -> timedelta:
    return timedelta(minutes=int(store_setting(MAX_HOLD_SETTING)))


def hold_limit(order: Order) -> datetime:
    """The latest instant any payment link may keep *order* payable.

    Counted from the order's creation, so opening new links (a retry, a
    second checkout session) can never stretch the hold past it.
    """
    return order.created_at + max_hold_window()


def deadline(order: Order, now: datetime | None = None) -> datetime:
    """When a payment opened *now* for *order* should stop being payable."""
    now = now or timezone.now()
    return min(now + hold_window(), hold_limit(order))


def window_closed(
    order: Order,
    minimum: timedelta = timedelta(0),
    now: datetime | None = None,
) -> bool:
    """True when no payment link of at least *minimum* fits before the limit."""
    return hold_limit(order) - (now or timezone.now()) <= minimum


def hold_ended(order: Order, now: datetime | None = None) -> bool:
    """True when *order*'s hold, plus the grace window, has ended: the
    instant :func:`expired_unpaid_online_orders` selects on, for one order."""
    ends = order.payment_hold_expires_at or (order.created_at + hold_window())
    return ends < (now or timezone.now()) - grace_window()


def unpaid_online_orders() -> QuerySet[Order]:
    """Online-payment orders still waiting for their payment.

    Collect-on-delivery orders are excluded: they legitimately sit PENDING
    until the carrier remits.
    """
    return Order.objects.filter(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        pay_way__settlement=PaySettlement.ONLINE,
    )


def expired_unpaid_online_orders(
    now: datetime | None = None,
) -> QuerySet[Order]:
    """Unpaid online orders whose hold, plus the grace window, has ended."""
    now = now or timezone.now()
    ends = Coalesce(
        "payment_hold_expires_at",
        ExpressionWrapper(
            F("created_at") + hold_window(), output_field=DateTimeField()
        ),
    )
    return (
        unpaid_online_orders()
        .alias(hold_ends=ends)
        .filter(hold_ends__lt=now - grace_window())
    )
