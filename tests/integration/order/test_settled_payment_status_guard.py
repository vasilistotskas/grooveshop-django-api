"""A settled payment state is final; polling the provider cannot undo it.

Providers report the state of the PAYMENT, which is not the state of the
money: a Stripe PaymentIntent stays ``succeeded`` forever after the
charge is refunded, and Viva's transaction lookup behaves the same way.
Every path that writes a polled status therefore has to refuse to move an
order out of COMPLETED / REFUNDED / PARTIALLY_REFUNDED / CANCELED — the
rule the webhook handlers already followed and the polling paths did not.

The customer-facing reach of this: ``GET /api/v1/order/{id}/payment_status``
is a read-only action on the order detail page, so a refunded order lost
its refund the next time its own owner opened the page.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from giftcard.factories import GiftCardPurchaseFactory
from order.enum.status import (
    SETTLED_PAYMENT_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.factories.order import OrderFactory
from order.models.history import OrderHistory
from order.models.order import AMOUNT_MISMATCH_FLAG
from order.models.payment_transaction import PaymentTransaction
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory

pytestmark = pytest.mark.django_db


def _order(payment_status):
    order = OrderFactory(num_order_items=0)
    order.payment_status = payment_status
    order.payment_id = "pi_test_123"
    order.save(update_fields=["payment_status", "payment_id"])
    return order


def _provider_says(status):
    provider = Mock()
    provider.get_payment_status.return_value = (status, {"id": "pi_test_123"})
    return patch("order.payment.get_payment_provider", return_value=provider)


@pytest.mark.parametrize("settled", sorted(SETTLED_PAYMENT_STATUSES))
def test_polling_never_moves_a_settled_order(settled):
    order = _order(settled)

    with _provider_says(PaymentStatus.COMPLETED):
        reported, _data = OrderService.get_payment_status(order)

    order.refresh_from_db()
    assert order.payment_status == settled
    # The provider's answer is still reported to the caller — only the
    # write is refused.
    assert reported == PaymentStatus.COMPLETED


def test_refund_survives_the_order_page_poll():
    """The concrete regression: a refunded order polled by its owner."""
    order = _order(PaymentStatus.REFUNDED)

    with _provider_says(PaymentStatus.COMPLETED):
        OrderService.get_payment_status(order)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.REFUNDED
    assert order.payment_status != PaymentStatus.COMPLETED


def _stripe_order(payment_status=PaymentStatus.PENDING):
    order = OrderFactory(num_order_items=2, status=OrderStatus.PENDING)
    order.pay_way = PayWayFactory(
        provider_code="stripe", settlement=PaySettlement.ONLINE
    )
    order.payment_status = payment_status
    order.payment_id = "pi_test_123"
    order.save(update_fields=["pay_way", "payment_status", "payment_id"])
    return order


def _stripe_says_paid(order, amount=None):
    owed = order.calculate_order_total_amount()
    provider = Mock()
    provider.get_payment_status.return_value = (
        PaymentStatus.COMPLETED,
        {
            "amount": float(owed.amount if amount is None else amount),
            "currency": str(owed.currency),
        },
    )
    return patch("order.payment.get_payment_provider", return_value=provider)


def test_polling_still_advances_an_unsettled_order():
    """The guard must not freeze the case polling exists for, and the
    advance goes through the webhook's handler: the charge is booked."""
    order = _stripe_order()

    with _stripe_says_paid(order):
        OrderService.get_payment_status(order)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.COMPLETED
    assert order.status == OrderStatus.PROCESSING
    assert PaymentTransaction.objects.filter(
        order=order, provider_reference="pi_test_123"
    ).exists()


def _payment_entries(order):
    return OrderHistory.objects.filter(order=order, change_type="PAYMENT")


def test_a_poll_that_settles_announces_as_the_webhook_does():
    """The history row and the confirmation email follow a payment the
    poll applied, exactly as they follow the webhook's."""
    order = _stripe_order()

    with (
        _stripe_says_paid(order),
        patch(
            "order.tasks.send_order_confirmation_email.apply_async"
        ) as confirmation,
    ):
        OrderService.get_payment_status(order)

    [entry] = _payment_entries(order)
    assert entry.previous_value == {
        "payment_status": PaymentStatus.PENDING.value
    }
    assert entry.new_value == {
        "payment_status": PaymentStatus.COMPLETED.value,
        "payment_id": "pi_test_123",
    }
    confirmation.assert_called_once()
    assert confirmation.call_args.kwargs["args"] == [order.id]


def test_a_poll_never_settles_for_an_amount_that_is_not_the_orders():
    order = _stripe_order()

    with (
        _stripe_says_paid(order, amount=Decimal("0.50")),
        patch(
            "order.tasks.send_order_confirmation_email.apply_async"
        ) as confirmation,
    ):
        OrderService.get_payment_status(order)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.PENDING
    assert AMOUNT_MISMATCH_FLAG in order.metadata
    # Nothing was applied, so nothing is announced.
    assert not _payment_entries(order).exists()
    confirmation.assert_not_called()


def test_a_poll_never_settles_an_intent_a_gift_card_purchase_holds():
    """The variant of the one-payment-two-things attack: an order that
    carries a gift-card intent must not be settled by whoever polls it."""
    order = _stripe_order()
    GiftCardPurchaseFactory(payment_id="pi_test_123")

    with _stripe_says_paid(order):
        OrderService.get_payment_status(order)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.PENDING


def test_a_poll_does_not_settle_a_provider_whose_webhook_settles():
    order = _stripe_order()
    order.pay_way = PayWayFactory(
        provider_code="viva_wallet", settlement=PaySettlement.ONLINE
    )
    order.save(update_fields=["pay_way"])

    with _stripe_says_paid(order):
        OrderService.get_payment_status(order)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.PENDING


def test_update_order_false_writes_nothing():
    order = _order(PaymentStatus.PENDING)

    with _provider_says(PaymentStatus.COMPLETED):
        OrderService.get_payment_status(order, update_order=False)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.PENDING
