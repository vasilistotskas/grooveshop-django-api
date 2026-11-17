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

The order detail serves both instants (:func:`hold_ends` and
:func:`decided_by`), so a client such as the agent gateway publishes when
the link stops being payable and when the order will have been settled one
way or the other, and keeps no window of its own.
"""

from datetime import UTC, datetime, timedelta

from celery.schedules import crontab
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import DateTimeField, ExpressionWrapper, F, QuerySet
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.celery import celery_app
from core.store_settings import store_setting
from order.enum.status import (
    SETTLED_PAYMENT_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.models.order import AMOUNT_MISMATCH_FLAG, Order
from order.payment import StripePaymentProvider, VivaWalletPaymentProvider
from pay_way.enum.settlement import PaySettlement
from scheduler.models import PeriodicTask

HOLD_SETTING = "ORDER_ONLINE_PAYMENT_HOLD_MINUTES"
MAX_HOLD_SETTING = "ORDER_ONLINE_PAYMENT_MAX_HOLD_MINUTES"
GRACE_SETTING = "ORDER_ONLINE_PAYMENT_GRACE_MINUTES"
FAILED_SETTING = "ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES"
ALERT_SETTING = "ORDER_UNPAID_ONLINE_ALERT_THRESHOLD"

#: The longest grace a store may keep. The grace is for a confirmation
#: already on its way when the link expires; one that arrives after the
#: cancel is still booked (``OrderService.record_payment_after_cancel``)
#: and refunded. Past this, a longer grace only holds stock nobody can pay
#: for any more.
MAX_GRACE = timedelta(hours=1)

#: The ``CELERY_BEAT_SCHEDULE`` entry that runs the sweep
#: (``order.tasks.auto_cancel_stuck_pending_orders``, fanned out per store).
SWEEP_ENTRY = "auto-cancel-stuck-pending-orders"

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


def validate_grace_minutes_setting(value: object) -> bool:
    """Validator for ``ORDER_ONLINE_PAYMENT_GRACE_MINUTES``.

    A whole number of minutes from 0 to :data:`MAX_GRACE`. A negative grace
    would release the stock before the payment link stops being payable,
    which is what the hold exists to prevent.
    """
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and timedelta(0) <= timedelta(minutes=value) <= MAX_GRACE
    )


def hold_window() -> timedelta:
    return timedelta(minutes=int(store_setting(HOLD_SETTING)))


def grace_window() -> timedelta:
    return timedelta(minutes=int(store_setting(GRACE_SETTING)))


def failed_window() -> timedelta:
    return timedelta(minutes=int(store_setting(FAILED_SETTING)))


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


def hold_ends(order: Order) -> datetime:
    """When *order*'s latest payment link stops being payable.

    The expiry the provider applied to the last link opened, or, before any
    was opened, the hold counted from the order's creation.
    """
    return order.payment_hold_expires_at or (order.created_at + hold_window())


def hold_ended(order: Order, now: datetime | None = None) -> bool:
    """True when *order*'s hold, plus the grace window, has ended: the
    instant :func:`expired_unpaid_online_orders` selects on, for one order."""
    return hold_ends(order) < (now or timezone.now()) - grace_window()


def awaiting_payment(order: Order) -> bool:
    """True when *order* holds its stock for an online payment.

    The one-order form of ``OrderQuerySet.holding_stock_for_online_payment``:
    a PENDING order of an online pay way whose payment is not final.
    """
    return (
        order.status == OrderStatus.PENDING
        and order.awaits_online_payment
        and order.payment_status not in SETTLED_PAYMENT_STATUSES
    )


def _next_sweep(after: datetime) -> datetime:
    """The first sweep run strictly after *after*, in UTC.

    Strictly: the sweep selects an order once its instant is in the past,
    so a run that starts exactly at it does not.

    Beat finds each run from the one before it with the crontab's
    ``remaining_delta``, which reads the wall clock of the beat zone
    (``CELERY_TIMEZONE``) and places a slot in a repeated hour at its
    first occurrence. So beat sends no run in the hour the clock goes back
    (``docs/scheduler.md``), and the slot that follows an instant inside
    that hour is one already past. Stepping through the same slots until
    one is after *after*, compared in UTC, gives the run beat sends. The
    run is returned in UTC so that what is added to it counts real time,
    not the wall clock across a change.
    """
    schedule = settings.CELERY_BEAT_SCHEDULE[SWEEP_ENTRY]["schedule"]
    if not isinstance(schedule, crontab):
        raise ImproperlyConfigured(
            f"The beat entry {SWEEP_ENTRY!r} must run on a crontab."
        )
    after = after.astimezone(UTC)
    run = after
    while run <= after:
        last_run_at, delta, _now = schedule.remaining_delta(run)
        run = (last_run_at + delta).astimezone(UTC)
    return run


def _sweep_time_limit() -> timedelta:
    """How long one store's sweep may run before the worker kills it.

    The worker's hard time limit (``CELERY_TASK_TIME_LIMIT``), which the
    prefork pool enforces from the moment a task starts executing. The
    sweep sets no limit of its own (a test holds it to that), and the
    fanout sends each store's run with none. The soft limit does not end
    the run: the sweep catches ``Exception`` per order, and
    ``SoftTimeLimitExceeded`` is one.
    """
    seconds = celery_app.conf.task_time_limit
    if not seconds:
        raise ImproperlyConfigured(
            "The unpaid-order sweep needs a hard time limit to promise "
            "when an order is decided (CELERY_TASK_TIME_LIMIT)."
        )
    return timedelta(seconds=seconds)


def decided_by(order: Order, now: datetime | None = None) -> datetime | None:
    """When the scheduled sweep will have decided *order*, running normally.

    The next ``auto_cancel_stuck_pending_orders`` run that selects the
    order, plus that run's hard time limit, by which every cancel it makes
    has committed. The sweep selects a pending payment once the hold plus
    the grace has passed; a failed one once the failure plus
    ``ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES`` has passed too, since a
    link that can still be paid is closed only once its hold has ended (a
    Stripe PaymentIntent, or an order with no pay way, is canceled sooner,
    so this is the latest). The run is never one already past: an order
    whose time came while the sweep was off, beat or the workers were
    down, or a run left it (a row another run held, an error) is decided
    by the next run after *now*. So the instant moves later whenever the
    sweep is paused or a run is cut short. Time a store's run waits in the
    queue before it starts is bounded by nothing and is not counted.

    ``None`` when nothing will decide the order: the sweep never selects
    it (:func:`swept_orders`), or the sweep is off: switched off
    (``scheduler.PeriodicTask``) or no longer declared in
    ``CELERY_BEAT_SCHEDULE`` (``PeriodicTask.objects.is_on``, asked before
    :func:`_next_sweep` reads the entry).
    """
    # Both buckets select PENDING orders only, so any other answers without
    # a query; the rest is the sweep's own querysets.
    if order.status != OrderStatus.PENDING:
        return None
    if not swept_orders().filter(pk=order.pk).exists():
        return None
    if not PeriodicTask.objects.is_on(SWEEP_ENTRY):
        return None
    selected_after = hold_ends(order) + grace_window()
    if order.payment_status == PaymentStatus.FAILED:
        selected_after = max(selected_after, order.updated_at + failed_window())
    now = now or timezone.now()
    return _next_sweep(max(selected_after, now)) + _sweep_time_limit()


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


def failed_payment_orders() -> QuerySet[Order]:
    """PENDING orders whose last payment attempt failed, of any pay way.

    The sweep's second bucket: a failed attempt does not end the payment
    (another card on the same PaymentIntent, a retry on the same Viva
    payment order), so the order waits
    ``ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES`` for one.
    """
    return Order.objects.filter(
        status=OrderStatus.PENDING, payment_status=PaymentStatus.FAILED
    )


def expired_failed_payment_orders(
    now: datetime | None = None,
) -> QuerySet[Order]:
    """Failed-payment orders not retried within the store's wait."""
    now = now or timezone.now()
    return failed_payment_orders().filter(updated_at__lt=now - failed_window())


def amount_mismatch_orders() -> QuerySet[Order]:
    """Orders with a confirmed charge that did not match their total.

    The Viva webhook refuses to mark such an order paid and stamps
    ``AMOUNT_MISMATCH_FLAG`` instead. The sweep never cancels one: it would
    close a CHARGED order without a refund. It needs a person.
    """
    return Order.objects.filter(metadata__has_key=AMOUNT_MISMATCH_FLAG)


def swept_orders() -> QuerySet[Order]:
    """Every order the sweep cancels once its time has come: its two
    buckets, less what it never cancels. The same definitions the sweep
    selects with, so :func:`decided_by` cannot promise more or less."""
    return (unpaid_online_orders() | failed_payment_orders()).exclude(
        pk__in=amount_mismatch_orders().values("pk")
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
