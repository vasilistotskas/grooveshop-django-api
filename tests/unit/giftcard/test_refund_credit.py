"""What goes back onto a gift card, and when.

``credit_refund`` used to credit ``-redeem.amount`` for every REDEEM row on
the order, once, when the order was REFUNDED: a EUR 100 order settled with
a EUR 60 card, a EUR 10 item returned and EUR 10 refunded by card put the
whole EUR 60 back. Staff now refund in the VALUE of what came back
(``OrderService.refund_order``): the provider's money first, then the cards,
in parts, so a card gets back exactly the part of the value that is beyond
the provider's share. This module is the card's side of that; the
order's side is ``tests/integration/order/test_value_based_refunds.py``.

A card is owed what it gave for the order (REDEEM) less what has come back
(REFUND_CREDIT), read under the card's row lock: that, not a unique
constraint, is what stops a credit being made twice.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from django.db import connection
from djmoney.money import Money

from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
from giftcard.services import GiftCardService
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import RefundTransactionFactory
from order.models.order import Order
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from order.signals import order_canceled, order_refunded
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


def _credits(order) -> list[Decimal]:
    return list(
        GiftCardTransaction.objects.filter(
            order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
        )
        .order_by("id")
        .values_list("amount", flat=True)
    )


class TestAProviderRefundLeavesTheCardAlone:
    """Refunding the provider's money returns nothing to a card: only the
    value beyond it does, and staff say how much."""

    def test_a_refund_row_credits_nothing_on_any_status(
        self, giftcard_order_settled_with_one_card
    ):
        order, _card = giftcard_order_settled_with_one_card
        for status in (
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
        ):
            order.payment_status = status
            order.save(update_fields=["payment_status"])
            order_refunded.send(
                sender=Order,
                order=order,
                amount=Money(Decimal("5.00"), "EUR"),
                refund=RefundTransactionFactory(
                    order=order, amount=Money(Decimal("5.00"), "EUR")
                ),
            )

        assert _credited(order) == Decimal(0)

    def test_viva_refunds_of_the_provider_money_do_not_touch_the_card(
        self, giftcard_order_settled_with_one_card
    ):
        """End to end from Viva's documented 1797: a EUR 100 order, EUR 60
        by gift card and EUR 40 charged. The EUR 5 and EUR 35 that refund
        all of Viva's money leave the card alone and the order partly
        refunded; the EUR 60 goes back when staff refund it."""
        order, card = giftcard_order_settled_with_one_card
        event = REVERSAL_CREATED_EVENT["EventData"]
        payment_id, order_code = event["ParentId"], event["OrderCode"]
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
            payment_id=payment_id,
            pay_way=PayWayFactory.create_online_payment("viva_wallet"),
            amount_due=Money(Decimal("40.00"), "EUR"),
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
                        retrieve_refund(
                            amount=paid_back, order_code=order_code
                        ),
                    )
                    for refund_id, paid_back in refunds.items()
                },
            }
        )

        def deliver(refund_id):
            body = reversal_created_event(
                refund_id=refund_id,
                parent_id=payment_id,
                order_code=order_code,
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
            deliver("last-refund")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert _credited(order) == Decimal(0)
        assert PaymentLedger.refundable_value(order) == Decimal("60.00")

        OrderService.refund_order(order)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert _credited(order) == Decimal("60.00")
        assert card.balance == Money(Decimal("60.00"), "EUR")


class TestCreditRefund:
    def test_everything_goes_back_once(
        self, giftcard_order_settled_with_one_card
    ):
        order, _card = giftcard_order_settled_with_one_card

        assert GiftCardService.credit_refund(order) == Decimal("60.00")
        assert GiftCardService.credit_refund(order) == Decimal(0)

        assert _credited(order) == Decimal("60.00")

    def test_a_part_goes_back_and_the_rest_is_still_owed(
        self, giftcard_order_settled_with_one_card
    ):
        order, _card = giftcard_order_settled_with_one_card

        assert GiftCardService.credit_refund(order, Decimal("25.00")) == (
            Decimal("25.00")
        )
        assert GiftCardService.credit_refund(order, Decimal("25.00")) == (
            Decimal("25.00")
        )

        assert _credits(order) == [Decimal("25.00"), Decimal("25.00")]
        assert GiftCardService.credit_refund(order) == Decimal("10.00")

    def test_never_more_than_the_card_gave(
        self, giftcard_order_settled_with_one_card
    ):
        order, _card = giftcard_order_settled_with_one_card

        credited = GiftCardService.credit_refund(order, Decimal("500.00"))

        assert credited == Decimal("60.00")
        assert GiftCardService.credit_refund(order, Decimal("1.00")) == 0
        assert _credited(order) == Decimal("60.00")

    def test_each_credit_names_the_refund_attempt(
        self, giftcard_order_settled_with_one_card
    ):
        from order.models.refund_attempt import RefundAttempt

        order, _card = giftcard_order_settled_with_one_card
        attempt = RefundAttempt.objects.create(
            order=order,
            charge=None,
            kind="STORED_VALUE",
            amount=Money(Decimal("10.00"), "EUR"),
        )

        GiftCardService.credit_refund(
            order, Decimal("10.00"), refund_attempt=attempt
        )

        [credit] = GiftCardTransaction.objects.filter(
            order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
        )
        assert credit.refund_attempt == attempt

    def test_each_card_gets_back_what_it_gave_when_everything_goes_back(
        self, giftcard_order_settled_with_two_cards
    ):
        order, first, second = giftcard_order_settled_with_two_cards

        GiftCardService.credit_refund(order)

        rows = {
            row.gift_card_id: row.amount
            for row in GiftCardTransaction.objects.filter(
                order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
            )
        }
        assert rows == {first.id: Decimal("30.00"), second.id: Decimal("10.00")}

    def test_a_part_is_shared_in_proportion_to_what_each_card_gave(
        self, giftcard_order_settled_with_two_cards
    ):
        order, first, second = giftcard_order_settled_with_two_cards

        GiftCardService.credit_refund(order, Decimal("20.00"))

        rows = {
            row.gift_card_id: row.amount
            for row in GiftCardTransaction.objects.filter(
                order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
            )
        }
        assert rows == {first.id: Decimal("15.00"), second.id: Decimal("5.00")}

    def test_the_cents_left_over_are_not_lost(self):
        from order.factories.order import OrderFactory

        order = OrderFactory(gift_card_amount=Money(Decimal("30.00"), "EUR"))
        cards = [
            GiftCardFactory(initial_value=Money(Decimal("10.00"), "EUR"))
            for _ in range(3)
        ]
        for card in cards:
            GiftCardTransaction.objects.create(
                gift_card=card,
                kind=GiftCardTransactionKind.REDEEM,
                amount=Decimal("-10.00"),
                order=order,
            )

        assert GiftCardService.credit_refund(order, Decimal("1.00")) == (
            Decimal("1.00")
        )

        assert sorted(_credits(order)) == [
            Decimal("0.33"),
            Decimal("0.33"),
            Decimal("0.34"),
        ]

    def test_an_expired_card_is_given_a_window_to_spend_the_refund(
        self, giftcard_order_settled_with_one_card
    ):
        from datetime import timedelta

        from django.utils import timezone

        order, card = giftcard_order_settled_with_one_card
        card.expires_at = timezone.now() - timedelta(days=1)
        card.save(update_fields=["expires_at"])

        GiftCardService.credit_refund(order, Decimal("10.00"))

        card.refresh_from_db()
        assert card.expires_at is None or card.expires_at > timezone.now()


@pytest.mark.django_db(transaction=True)
class TestTwoCreditsRacing:
    def test_a_card_is_credited_no_more_than_it_gave(
        self, giftcard_order_settled_with_one_card
    ):
        order, _card = giftcard_order_settled_with_one_card
        results: list[Decimal] = []

        def credit():
            try:
                results.append(GiftCardService.credit_refund(order))
            finally:
                connection.close()

        threads = [threading.Thread(target=credit) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sum(results, Decimal(0)) == Decimal("60.00")
        assert _credited(order) == Decimal("60.00")


class TestACanceledOrderGivesTheRedemptionBack:
    def test_the_cancel_credits_everything_once(
        self, giftcard_order_settled_with_one_card
    ):
        order, card = giftcard_order_settled_with_one_card

        order_canceled.send(sender=Order, order=order)
        order_canceled.send(sender=Order, order=order)

        assert _credited(order) == Decimal("60.00")
        assert card.balance == Money(Decimal("60.00"), "EUR")
