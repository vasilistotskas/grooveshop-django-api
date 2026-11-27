"""Settling a paid Stripe Checkout Session: the code its
``checkout.session.completed`` webhook runs
(``order.signals.handlers.handle_stripe_checkout_completed``), and the code
that settles a session read back from Stripe when that webhook did not land
(``StripePaymentProvider.settle_landed_payment``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import stripe
from django.db import transaction
from djmoney.money import Money

from order.enum.landed_payment import LandedPayment
from order.enum.status import (
    SETTLED_PAYMENT_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.exceptions import PaymentStatusUnavailableError
from order.models.history import OrderHistory
from order.models.order import Order
from order.payments import payment_hold
from order.payments.cart_clear import (
    CART_UNSET,
    CartUnsetType,
    clear_cart_for_order,
    clear_locked_cart_for_order,
    lock_cart_for_order_id,
)
from order.payments.payment import (
    StripePaymentProvider,
    money_from_minor_units,
)
from order.payments.payment_ledger import (
    PAYMENT_AMOUNT_TOLERANCE,
    PaymentLedger,
)

if TYPE_CHECKING:
    from cart.models import Cart

logger = logging.getLogger(__name__)


def settle_paid_checkout_session(
    order: Order,
    *,
    session_id: str,
    payment_intent_id: str,
    captured: Money | None,
    cart: Cart | None | CartUnsetType = CART_UNSET,
) -> None:
    """Settle *order*, locked by the caller, for the paid Checkout Session
    *session_id*: book the charge, then mark the order paid unless it was
    canceled, paid by another intent, already settled, or paid another
    amount. Saves the order's ``metadata`` the caller filled, and clears
    the order's lines from *cart* inside the same transaction (#839).

    Then the order is confirmed whichever way it went: a session seen
    again (a redelivery, a read-back) confirms an order paid but not yet
    confirmed, and ``OrderLifecycleService.confirm`` leaves a confirmed,
    unpaid or canceled one as it is.
    """
    from order.lifecycle.service import OrderLifecycleService

    _settle_paid_checkout_session(
        order,
        session_id=session_id,
        payment_intent_id=payment_intent_id,
        captured=captured,
        cart=cart,
    )
    OrderLifecycleService.confirm(order)


def _settle_paid_checkout_session(
    order: Order,
    *,
    session_id: str,
    payment_intent_id: str,
    captured: Money | None,
    cart: Cart | None | CartUnsetType = CART_UNSET,
) -> None:
    from order.lifecycle.service import OrderLifecycleService
    from order.payments.payment_events import publish_payment_status
    from order.services import OrderService
    from shipping.services import ShippingService

    order.metadata["stripe_checkout_session_id"] = session_id
    order.metadata["stripe_payment_intent_id"] = payment_intent_id

    # The money is in whatever state the order is. The
    # ``payment_intent.succeeded`` event that follows records the
    # same charge under the same key and changes nothing.
    if captured is not None:
        PaymentLedger.record_charge(
            order,
            amount=captured,
            provider=StripePaymentProvider.code,
            reference=payment_intent_id,
        )

    # Before the settled guard: a canceled order's payment is
    # already settled, and behind the guard a real charge on it
    # was dropped with a WARNING. Never ships (G0281).
    if OrderService.is_payment_after_cancel(order):
        OrderService.record_payment_after_cancel(
            order,
            payment_id=payment_intent_id,
            payment_method="stripe",
        )
        publish_payment_status(order)
        return

    # A second payment on an order another PaymentIntent already
    # paid: booked above, and ``PaymentLedger.sync_overcharge``
    # told staff when it took the order past what it is for. The
    # order keeps its first ``payment_id``.
    if (
        order.payment_status == PaymentStatus.COMPLETED
        and order.payment_id
        and order.payment_id != payment_intent_id
    ):
        logger.error(
            "PaymentIntent %s is a second payment on order %s, "
            "already paid by %s: recorded in the ledger, order "
            "left as it is",
            payment_intent_id,
            order.id,
            order.payment_id,
        )
        order.save(update_fields=["metadata"])
        return

    # Already settled by this very payment: a redelivery, or the webhook of a
    # payment ``settle_landed_payment`` read back from Stripe before it
    # arrived. Writing it again would log it, ship it and confirm it twice.
    if (
        order.payment_status == PaymentStatus.COMPLETED
        and order.payment_id == payment_intent_id
    ):
        logger.info(
            "Checkout session %s of order %s is already settled",
            session_id,
            order.id,
        )
        order.save(update_fields=["metadata"])
        return

    # Settled-state guard: Stripe does not guarantee event
    # delivery order, so a delayed checkout.session.completed
    # must never un-refund / un-cancel an order that already
    # reached a settled financial state. Mirrors
    # OrderService.handle_payment_succeeded.
    # COMPLETED is excluded on purpose: a "paid" event landing on
    # an already-completed order is a redelivery, and processing
    # it again is a harmless no-op rather than a regression.
    if order.payment_status in (
        SETTLED_PAYMENT_STATUSES - {PaymentStatus.COMPLETED}
    ):
        logger.warning(
            "Ignoring checkout.session.completed for order %s: "
            "payment_status already %s",
            order.id,
            order.payment_status,
        )
        # Persist the webhook_processed flag set above so a
        # Stripe redelivery short-circuits on the idempotency
        # check instead of re-running this guard (harmless, but
        # avoids duplicate warning logs on every retry).
        order.save(update_fields=["metadata"])
        return

    # What Stripe collected must be what the order is for: a
    # session paid for another amount settles nothing.
    expected = order.calculate_order_total_amount()
    if captured is not None and (
        captured.currency != expected.currency
        or abs(captured.amount - expected.amount) > PAYMENT_AMOUNT_TOLERANCE
    ):
        OrderService.flag_payment_mismatch(
            order,
            payment_id=payment_intent_id,
            provider=StripePaymentProvider.code,
            observed=captured,
            expected=expected,
        )
        publish_payment_status(order)
        return

    previous_payment_status = order.payment_status
    order.mark_as_paid(payment_id=payment_intent_id, payment_method="stripe")
    order.save(update_fields=["metadata"])
    if previous_payment_status != PaymentStatus.COMPLETED:
        if isinstance(cart, CartUnsetType):
            clear_cart_for_order(order)
        else:
            clear_locked_cart_for_order(order, cart)

    OrderHistory.log_payment_update(
        order=order,
        previous_value={"payment_status": previous_payment_status},
        new_value={
            "payment_status": PaymentStatus.COMPLETED,
            "payment_id": payment_intent_id,
            "checkout_session_id": session_id,
        },
    )

    if order.status == OrderStatus.PENDING:
        OrderLifecycleService.update_order_status(order, OrderStatus.PROCESSING)

    # Stripe's guidance is to fulfil hosted Checkout Sessions on
    # checkout.session.completed (not payment_intent.succeeded).
    # Dispatch the courier task here so a Stripe-Checkout order
    # isn't left paid-but-never-shipped when the PaymentIntent
    # event's payment_id lookup races/misses. Idempotent on the
    # shipment row; wrapped in on_commit by ShippingService.
    ShippingService.dispatch_create_shipment_task(order)

    publish_payment_status(order)

    logger.info(
        "Order %s marked as paid via checkout session %s",
        order.id,
        session_id,
    )


def settle_checkout_session(
    order_id: int,
    *,
    session_id: str,
    payment_intent_id: str,
    captured: Money,
) -> None:
    """:func:`settle_paid_checkout_session` for a session read back from
    Stripe, locking the cart before the order row as checkout does."""
    with transaction.atomic():
        cart = lock_cart_for_order_id(order_id)
        order = Order.objects.select_for_update().get(pk=order_id)
        if not order.metadata:
            order.metadata = {}
        settle_paid_checkout_session(
            order,
            session_id=session_id,
            payment_intent_id=payment_intent_id,
            captured=captured,
            cart=cart,
        )


def settle_landed_payment(order: Order) -> LandedPayment:
    """Settle what Stripe took for *order* whose webhook has not landed,
    and say what Stripe holds (``LandedPayment``).

    A succeeded PaymentIntent is settled as a poll settles one
    (``OrderService.settle_polled_completion``: the
    ``payment_intent.succeeded`` handler and its announcement). Otherwise
    the hosted Checkout Session the order recorded is read: opening one
    cancels the order's open intent and leaves its ``payment_id``, so a
    canceled intent does not mean nothing was paid. A paid session is
    settled by its ``checkout.session.completed`` code
    (:func:`settle_checkout_session`). A complete session whose payment is
    still in flight (a delayed method) settled nothing yet and keeps the
    order (``UNSETTLED``) for its webhook; a payment taken that settled
    nothing is flagged for the store's review (:func:`_what_settled`).

    Raises ``PaymentStatusUnavailableError`` when Stripe could not be read.
    """
    provider = StripePaymentProvider()
    payment_id = order.payment_id or ""
    if payment_id.startswith("pi_"):
        try:
            intent = stripe.PaymentIntent.retrieve(
                payment_id, api_key=provider.api_key
            )
        except stripe.StripeError as e:
            logger.warning("PaymentIntent %s could not be read", payment_id)
            raise PaymentStatusUnavailableError(payment_id) from e
        if intent.status == "succeeded":
            from order.services import OrderService

            received = money_from_minor_units(
                intent.amount_received, intent.currency
            )
            OrderService.settle_polled_completion(
                order,
                {
                    "amount": received.amount,
                    "currency": str(received.currency),
                },
            )
            return _what_settled(order, payment_id, received)

    session_id = provider.recorded_checkout_session(order.metadata or {})
    if not session_id:
        return LandedPayment.NONE
    try:
        session = stripe.checkout.Session.retrieve(
            session_id, api_key=provider.api_key, expand=["payment_intent"]
        )
    except stripe.StripeError as e:
        logger.warning("Checkout Session %s could not be read", session_id)
        raise PaymentStatusUnavailableError(session_id) from e
    if session.status != "complete":
        return LandedPayment.NONE
    intent = session.payment_intent
    intent_id = intent if isinstance(intent, str) else getattr(intent, "id", "")
    if session.payment_status != "paid" or not intent_id:
        if not StripePaymentProvider.session_payment_may_land(session):
            return LandedPayment.NONE
        logger.warning(
            "Checkout Session %s of order %s is complete and its payment "
            "is still in flight: the order is kept for its webhook",
            session_id,
            order.id,
        )
        return LandedPayment.UNSETTLED
    if session.amount_total is None or not session.currency:
        # A paid session states what it collected; one that does not is
        # an answer that could not be read.
        logger.warning("Checkout Session %s states no amount", session_id)
        raise PaymentStatusUnavailableError(session_id)
    captured = money_from_minor_units(session.amount_total, session.currency)
    settle_checkout_session(
        order.pk,
        session_id=session_id,
        payment_intent_id=intent_id,
        captured=captured,
    )
    return _what_settled(order, intent_id, captured)


def _what_settled(
    order: Order, payment_id: str, captured: Money
) -> LandedPayment:
    """``SETTLED`` once the payment Stripe took (*payment_id*, *captured*)
    left *order* paid or waiting for the store's review. One that settled
    nothing is flagged for that review (``OrderService.flag_payment_mismatch``,
    under the order's lock), never left for the next run to repeat."""
    from order.services import OrderService

    order.refresh_from_db()
    if order.payment_status == PaymentStatus.COMPLETED or (
        payment_hold.awaits_payment_review(order)
    ):
        return LandedPayment.SETTLED
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if (
            locked.status == OrderStatus.PENDING
            and locked.payment_status != PaymentStatus.COMPLETED
        ):
            OrderService.flag_payment_mismatch(
                locked,
                payment_id=payment_id,
                provider=StripePaymentProvider.code,
                observed=captured,
                expected=locked.calculate_order_total_amount(),
            )
    return LandedPayment.SETTLED
