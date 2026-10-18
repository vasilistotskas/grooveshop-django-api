"""Refunds in the value of what came back (owner decision, 2026-10-18).

Staff enter the value the customer returned. The provider's money is paid
back first; anything beyond it goes back to what the order paid with gift
cards and points, in proportion to how much each gave. Refunding only the
provider's share therefore no longer hands back the whole gift-card
redemption: a EUR 90 gift card plus EUR 10 card order, a EUR 10 item
returned, gets EUR 10 back by card and nothing on the gift card.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models.refund_attempt import RefundAttempt, RefundAttemptStatus
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from pay_way.factories import PayWayFactory
from tests.utils.stripe_refunds import FakeStripeRefunds

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _eur(amount: str | int) -> Money:
    return Money(Decimal(str(amount)), "EUR")


def _order(
    *,
    card: str = "40.00",
    gift: str = "60.00",
    points_value: str = "0.00",
    points: int = 0,
    status: OrderStatus = OrderStatus.DELIVERED,
):
    """An order paid *card* by Stripe, *gift* by one gift card and
    *points_value* by *points* loyalty points."""
    from user.factories.account import UserAccountFactory

    user = UserAccountFactory(num_addresses=0)
    order = OrderFactory(
        user=user,
        num_order_items=0,
        # An order paid with no provider has neither a pay way nor a payment.
        pay_way=PayWayFactory.create_online_payment()
        if Decimal(card) > 0
        else None,
        status=status,
        payment_status=PaymentStatus.COMPLETED,
        payment_id="pi_value" if Decimal(card) > 0 else "",
        amount_due=_eur(card),
        gift_card_amount=_eur(gift),
        loyalty_discount=_eur(points_value),
    )
    if Decimal(card) > 0:
        PaymentTransactionFactory(
            order=order, amount=_eur(card), provider_reference="pi_value"
        )
    cards = []
    if Decimal(gift) > 0:
        gift_card = GiftCardFactory(initial_value=_eur(gift))
        GiftCardTransaction.objects.create(
            gift_card=gift_card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=-Decimal(gift),
            order=order,
        )
        cards.append(gift_card)
    if points:
        PointsTransaction.objects.create(
            user=user,
            points=points,
            transaction_type=TransactionType.EARN,
        )
        PointsTransaction.objects.create(
            user=user,
            points=-points,
            transaction_type=TransactionType.REDEEM,
            reference_order=order,
        )
    return order, cards


def _status(order) -> str:
    order.refresh_from_db()
    return order.payment_status


def _credited(order) -> Decimal:
    return sum(
        (
            row.amount
            for row in GiftCardTransaction.objects.filter(
                order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
            )
        ),
        Decimal(0),
    )


def _restored(order) -> int:
    return sum(
        PointsTransaction.objects.filter(
            reference_order=order,
            transaction_type=TransactionType.ADJUST,
            points__gt=0,
        ).values_list("points", flat=True)
    )


class TestTheProviderIsRefundedFirst:
    def test_refunding_the_card_share_leaves_the_gift_card_alone(self):
        order, _cards = _order()

        with FakeStripeRefunds() as stripe_api:
            result = OrderService.refund_order(order, amount=_eur("40.00"))

        assert result.complete is True
        assert stripe_api.amounts == [4000]
        assert _credited(order) == 0
        assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED
        assert PaymentLedger.refundable_value(order) == Decimal("60.00")

    def test_a_refund_below_the_card_share_is_all_card(self):
        order, _cards = _order()

        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, amount=_eur("10.00"))

        assert stripe_api.amounts == [1000]
        assert _credited(order) == 0

    def test_the_whole_value_returns_card_and_gift_card_and_completes(
        self, refunded_signals
    ):
        order, [card] = _order()

        with FakeStripeRefunds() as stripe_api:
            result = OrderService.refund_order(order, amount=_eur("100.00"))

        assert result.complete is True
        assert result.refunded == _eur("40.00")
        assert result.stored == _eur("60.00")
        assert stripe_api.amounts == [4000]
        assert _credited(order) == Decimal("60.00")
        assert card.balance == _eur("60.00")
        assert _status(order) == PaymentStatus.REFUNDED
        assert PaymentLedger.refundable_value(order) == 0
        # One signal for the card refund, none for stored value (no money
        # moved through a provider).
        assert len(refunded_signals) == 1

    def test_nothing_named_returns_everything(self):
        order, _cards = _order()

        with FakeStripeRefunds() as stripe_api:
            result = OrderService.refund_order(order)

        assert result.refunded == _eur("40.00")
        assert result.stored == _eur("60.00")
        assert stripe_api.amounts == [4000]
        assert _credited(order) == Decimal("60.00")
        assert _status(order) == PaymentStatus.REFUNDED


class TestInParts:
    def test_the_excess_over_the_card_credits_the_gift_card_in_parts(self):
        order, [card] = _order()

        with FakeStripeRefunds() as stripe_api:
            first = OrderService.refund_order(order, amount=_eur("70.00"))
            assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED
            second = OrderService.refund_order(order, amount=_eur("30.00"))

        assert first.refunded == _eur("40.00")
        assert first.stored == _eur("30.00")
        assert second.refunded == _eur("0.00")
        assert second.stored == _eur("30.00")
        assert stripe_api.amounts == [4000]
        assert _credited(order) == Decimal("60.00")
        assert card.balance == _eur("60.00")
        assert _status(order) == PaymentStatus.REFUNDED
        # Two credits on one card for one order: the ledger allows it.
        assert (
            GiftCardTransaction.objects.filter(
                order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
            ).count()
            == 2
        )

    def test_each_credit_names_the_refund_that_made_it(self):
        order, _cards = _order()

        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("70.00"))

        [credit] = GiftCardTransaction.objects.filter(
            order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
        )
        attempt = credit.refund_attempt
        assert attempt is not None
        assert attempt.amount == _eur("30.00")
        assert attempt.status == RefundAttemptStatus.SETTLED

    def test_two_cards_are_credited_what_each_gave(self):
        order, [first] = _order(card="20.00", gift="30.00")
        second = GiftCardFactory(initial_value=_eur("50.00"))
        GiftCardTransaction.objects.create(
            gift_card=second,
            kind=GiftCardTransactionKind.REDEEM,
            amount=Decimal("-50.00"),
            order=order,
        )
        Order = type(order)
        Order.objects.filter(pk=order.pk).update(gift_card_amount=_eur("80.00"))
        order.refresh_from_db()

        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("60.00"))

        # 20 card, then 40 of the 80 on cards, split by what is left on
        # each (30 : 50), to the cent: 15.00 and 25.00.
        assert first.balance == _eur("15.00")
        assert second.balance == _eur("25.00")


class TestWhatCannotBeRefunded:
    def test_more_than_the_card_and_the_stored_value_is_refused(self):
        order, _cards = _order()

        with FakeStripeRefunds() as stripe_api, pytest.raises(PaymentError):
            OrderService.refund_order(order, amount=_eur("100.01"))

        assert stripe_api.calls == []
        assert _credited(order) == 0

    def test_a_declined_card_refund_leaves_the_gift_card_alone(self):
        order, _cards = _order()

        with FakeStripeRefunds(status="failed"):
            result = OrderService.refund_order(order, amount=_eur("100.00"))

        assert result.complete is False
        assert result.stored == _eur("0.00")
        assert _credited(order) == 0
        assert {a.status for a in order.refund_attempts.all()} == {
            RefundAttemptStatus.DECLINED
        }
        assert PaymentLedger.refundable_value(order) == Decimal("100.00")

    def test_an_open_dispute_holds_the_stored_value_too(self):
        order, _cards = _order()
        order.metadata = {
            "disputed": True,
            "dispute_id": "dp_1",
            "dispute_status": "needs_response",
        }
        order.save(update_fields=["metadata"])

        with FakeStripeRefunds(), pytest.raises(PaymentError):
            OrderService.refund_order(order, amount=_eur("100.00"))

        assert _credited(order) == 0


class TestAnOrderPaidEntirelyWithGiftCards:
    def test_it_can_be_refunded_onto_the_card(self):
        order, [card] = _order(card="0.00", gift="25.00")

        result = OrderService.refund_order(order, amount=_eur("25.00"))

        assert result.complete is True
        assert result.refunded == _eur("0.00")
        assert result.stored == _eur("25.00")
        assert card.balance == _eur("25.00")
        assert _status(order) == PaymentStatus.REFUNDED

    def test_it_is_open_until_it_is_refunded(self):
        order, _cards = _order(card="0.00", gift="25.00")

        assert PaymentLedger.refund_open(order) is True
        OrderService.refund_order(order)
        assert PaymentLedger.refund_open(order) is False


class TestPointsGoBackInProportion:
    """EUR 40 card, EUR 40 gift card and EUR 20 of points (2000 points):
    the excess over the card splits 40 : 20 between card and points."""

    def _paid(self):
        return _order(
            card="40.00", gift="40.00", points_value="20.00", points=2000
        )

    def test_a_part_refund_returns_a_part_of_each(self):
        order, _cards = self._paid()

        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("70.00"))

        # 40 to the card, 30 of the 60 stored: 20 on the gift card and 10
        # of the 20 of points, which is 1000 of the 2000.
        assert _credited(order) == Decimal("20.00")
        assert _restored(order) == 1000
        assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED

    def test_the_rest_returns_exactly_what_is_left(self):
        order, _cards = self._paid()

        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("70.00"))
            OrderService.refund_order(order, amount=_eur("30.00"))

        assert _credited(order) == Decimal("40.00")
        assert _restored(order) == 2000
        assert _status(order) == PaymentStatus.REFUNDED

    def test_a_rounding_remainder_is_not_lost_on_the_last_refund(self):
        order, _cards = _order(
            card="10.00", gift="10.00", points_value="10.00", points=999
        )

        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("11.00"))
            OrderService.refund_order(order, amount=_eur("11.00"))
            # The last refund asks for what is left (the dialog's default),
            # whatever rounding the parts before it left behind.
            OrderService.refund_order(order)

        assert _credited(order) == Decimal("10.00")
        assert _restored(order) == 999

    def test_the_points_come_back_with_a_balance_the_customer_can_spend(self):
        from loyalty.services import LoyaltyService

        order, _cards = self._paid()
        user = order.user
        assert LoyaltyService.get_user_balance(user) == 0

        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("100.00"))

        assert LoyaltyService.get_user_balance(user) == 2000


class TestARepeatIsNotASecondRefund:
    def test_a_stored_share_is_credited_once_when_asked_twice(self):
        order, _cards = _order(card="0.00", gift="25.00")
        OrderService.refund_order(order, amount=_eur("10.00"))

        OrderService.refund_order(order, amount=_eur("10.00"))

        assert _credited(order) == Decimal("20.00")
        with pytest.raises(PaymentError):
            OrderService.refund_order(order, amount=_eur("10.00"))
        assert _credited(order) == Decimal("20.00")

    def test_a_crash_before_the_credit_is_resumed_not_repeated(self):
        order, _cards = _order(card="0.00", gift="25.00")
        attempt = RefundAttempt.objects.create(
            order=order,
            charge=None,
            kind="STORED_VALUE",
            amount=_eur("25.00"),
        )

        result = OrderService.refund_order(order, amount=_eur("25.00"))

        assert result.stored == _eur("25.00")
        assert order.refund_attempts.count() == 1
        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.SETTLED
        assert _credited(order) == Decimal("25.00")


class TestACancelAndARefundShareOneRedemption:
    def test_the_redemption_goes_back_once(self):
        order, [card] = _order(status=OrderStatus.PROCESSING)

        with FakeStripeRefunds():
            OrderService.cancel_order(order, customer_initiated=False)

        assert _credited(order) == Decimal("60.00")
        assert card.balance == _eur("60.00")


class TestClosingAsRefunded:
    def test_it_waits_for_the_stored_value_too(self):
        from order.exceptions import RefundOutstandingError

        order, _cards = _order(status=OrderStatus.RETURNED)
        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("40.00"))

        with pytest.raises(RefundOutstandingError):
            OrderService.update_order_status(order, OrderStatus.REFUNDED)

        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("60.00"))
        OrderService.update_order_status(order, OrderStatus.REFUNDED)
        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED


class TestTheLastReturnCompletesTheOrder:
    def _earned(self, order, points=50):
        PointsTransaction.objects.create(
            user=order.user,
            points=points,
            transaction_type=TransactionType.EARN,
            reference_order=order,
        )

    @staticmethod
    def _reversed(order) -> int:
        return -sum(
            PointsTransaction.objects.filter(
                reference_order=order,
                transaction_type=TransactionType.ADJUST,
                points__lt=0,
            ).values_list("points", flat=True)
        )

    def test_the_points_it_earned_come_off_with_the_last_part_only(self):
        order, _cards = _order(
            card="40.00", gift="40.00", points_value="20.00", points=2000
        )
        self._earned(order)

        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("70.00"))
            assert self._reversed(order) == 0
            OrderService.refund_order(order, amount=_eur("30.00"))

        assert self._reversed(order) == 50

    def test_a_refund_of_the_card_alone_does_not_reverse_them(self):
        """The provider's money is back but the gift card and points are
        not: the order is not refunded, so what it earned stays."""
        order, _cards = _order()
        self._earned(order)

        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("40.00"))

        assert self._reversed(order) == 0
        assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED

    def test_the_history_says_what_went_back_onto_cards_and_points(self):
        from order.models.history import OrderHistory

        order, _cards = _order(
            card="40.00", gift="40.00", points_value="20.00", points=2000
        )

        with FakeStripeRefunds():
            OrderService.refund_order(
                order, amount=_eur("70.00"), reason="Two items back"
            )

        [note] = [
            row.new_value["note"]
            for row in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
            if row.new_value["note"].startswith("Returned ")
        ]
        assert "20.00" in note or "20,00" in note
        assert "1000 loyalty points" in note
        assert "Two items back" in note

    def test_no_refund_email_is_sent_for_value_that_moved_no_money(self):
        from django.core import mail

        order, _cards = _order(card="0.00", gift="25.00")

        OrderService.refund_order(order, amount=_eur("25.00"))

        assert not [
            m
            for m in mail.outbox
            if "refund_confirmation" in m.extra_headers.get("List-ID", "")
        ]


class TestAClaimedStoredShareIsHeldBack:
    def test_a_pending_share_cannot_be_refunded_again(self):
        order, _cards = _order(card="0.00", gift="25.00")
        RefundAttempt.objects.create(
            order=order,
            charge=None,
            kind="STORED_VALUE",
            amount=_eur("20.00"),
        )

        assert PaymentLedger.refundable_value(order) == Decimal("5.00")
        with pytest.raises(PaymentError):
            OrderService.refund_order(order, amount=_eur("6.00"))
        assert _credited(order) == 0
