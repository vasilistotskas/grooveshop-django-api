"""A refund must never put back more than it took out.

`credit_refund` used to credit `-redeem.amount` for every REDEEM row on
the order — the whole redeemed value — whatever was actually refunded. A
EUR 100 order settled with a EUR 60 gift card, then refunded EUR 5 as
goodwill, put the entire EUR 60 back on the card, immediately spendable,
while the shopper kept the goods. EUR 60 created out of nothing.

The redemption now goes back ONCE, with the refund that completes the
order (``payment_status`` REFUNDED). A partial refund is paid back by the
provider and leaves the card alone: crediting a slice as well would hand
back more than that refund was worth, and the one-credit-per-card ledger
could not top the card up when the rest is refunded.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.models.order import Order
from order.signals import order_refunded
from order.views.viva_webhook import _process_event_in_tenant
from pay_way.factories import PayWayFactory
from tests.utils.viva import (
    REVERSAL_CREATED_EVENT,
    FakeVivaApi,
    retrieve_refund,
    retrieve_transaction,
    reversal_created_event,
)

pytestmark = pytest.mark.django_db


def _credited(order) -> Decimal:
    rows = GiftCardTransaction.objects.filter(
        order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
    )
    return sum((row.amount for row in rows), Decimal(0))


def _refund(order, status, amount):
    """What every refund path does: write the status, then signal."""
    order.payment_status = status
    order.save(update_fields=["payment_status"])
    order_refunded.send(
        sender=Order, order=order, amount=Money(Decimal(amount), "EUR")
    )


def test_a_partial_refund_credits_nothing(
    giftcard_order_settled_with_one_card,
):
    order, _card = giftcard_order_settled_with_one_card

    _refund(order, PaymentStatus.PARTIALLY_REFUNDED, "5.00")

    assert _credited(order) == Decimal(0), (
        "a partial refund must not return any of the redemption"
    )


def test_the_refund_that_completes_the_order_credits_the_whole_redemption(
    giftcard_order_settled_with_one_card,
):
    """Whatever the last slice was: the amount on the signal is not the
    credit, the redemption is."""
    order, _card = giftcard_order_settled_with_one_card

    _refund(order, PaymentStatus.REFUNDED, "5.00")

    assert _credited(order) == Decimal("60.00")


def test_a_refund_signalled_twice_credits_once(
    giftcard_order_settled_with_one_card,
):
    order, _card = giftcard_order_settled_with_one_card

    _refund(order, PaymentStatus.REFUNDED, "40.00")
    _refund(order, PaymentStatus.REFUNDED, "40.00")

    assert _credited(order) == Decimal("60.00")


def test_each_card_gets_back_what_it_gave(
    giftcard_order_settled_with_two_cards,
):
    order, first, second = giftcard_order_settled_with_two_cards

    _refund(order, PaymentStatus.REFUNDED, "40.00")

    rows = {
        row.gift_card_id: row.amount
        for row in GiftCardTransaction.objects.filter(
            order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
        )
    }
    assert rows == {first.id: Decimal("30.00"), second.id: Decimal("10.00")}


def test_viva_refunds_return_the_redemption_with_the_last_one(
    giftcard_order_settled_with_one_card,
):
    """End to end from Viva's documented 1797: a EUR 100 order, EUR 60 by
    gift card and EUR 40 charged. A EUR 5 refund leaves the card alone;
    the EUR 35 that completes the refund returns the EUR 60, once."""
    order, _card = giftcard_order_settled_with_one_card
    event = REVERSAL_CREATED_EVENT["EventData"]
    payment_id, order_code = event["ParentId"], event["OrderCode"]
    Order.objects.filter(pk=order.pk).update(
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.COMPLETED,
        payment_id=payment_id,
        pay_way=PayWayFactory.create_online_payment("viva_wallet"),
        paid_amount=Money(Decimal("40.00"), "EUR"),
        metadata={"viva_order_codes": [str(order_code)]},
    )
    refunds = {"first-refund": 5.0, "last-refund": 35.0}
    viva = FakeVivaApi(
        transactions={
            payment_id: (
                200,
                retrieve_transaction(
                    amount=40.0, orderCode=order_code, statusId="R"
                ),
            ),
            **{
                refund_id: (
                    200,
                    retrieve_refund(amount=paid_back, order_code=order_code),
                )
                for refund_id, paid_back in refunds.items()
            },
        }
    )

    def deliver(refund_id):
        body = reversal_created_event(
            refund_id=refund_id, parent_id=payment_id, order_code=order_code
        )
        data = body["EventData"]
        return _process_event_in_tenant(
            order_code=str(order_code),
            event_type_id=body["EventTypeId"],
            event_data=data,
            transaction_id=refund_id,
            status_id=data["StatusId"],
            txn_hash="deadbeef",
        )

    with viva:
        deliver("first-refund")
        assert _credited(order) == Decimal(0)
        deliver("last-refund")

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.REFUNDED
    assert _credited(order) == Decimal("60.00")
