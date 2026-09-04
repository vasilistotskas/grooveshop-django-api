"""A refund must never put back more than it took out.

`credit_refund` used to credit `-redeem.amount` for every REDEEM row on
the order — the whole redeemed value — with no reference to how much was
actually refunded. A EUR 100 order settled with a EUR 60 gift card, then
refunded EUR 5 as goodwill, put the entire EUR 60 back on the card,
immediately spendable, while the shopper kept the goods. EUR 60 created
out of nothing, once per order.

Two senders reach it. `OrderService.refund_order` passes `amount=` to
`order_refunded` and the gift-card handler dropped it. The Viva reversal
path fires on a verified `PARTIALLY_REFUNDED` with no amount at all.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
from giftcard.services import GiftCardService

pytestmark = pytest.mark.django_db


def _credited(order) -> Decimal:
    rows = GiftCardTransaction.objects.filter(
        order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
    )
    return sum((row.amount for row in rows), Decimal(0))


def test_partial_refund_credits_only_what_came_back(
    giftcard_order_settled_with_one_card,
):
    order, _card = giftcard_order_settled_with_one_card

    GiftCardService.credit_refund(order, Decimal("5.00"))

    assert _credited(order) == Decimal("5.00"), (
        "a partial refund must not return the whole redemption"
    )


def test_full_refund_still_credits_everything(
    giftcard_order_settled_with_one_card,
):
    order, _card = giftcard_order_settled_with_one_card

    GiftCardService.credit_refund(order, None)

    assert _credited(order) == Decimal("60.00")


def test_a_refund_larger_than_the_redemption_is_capped(
    giftcard_order_settled_with_one_card,
):
    """The provider settled the rest; the card only gets back its own."""
    order, _card = giftcard_order_settled_with_one_card

    GiftCardService.credit_refund(order, Decimal("100.00"))

    assert _credited(order) == Decimal("60.00")


def test_partial_refund_splits_across_cards_and_sums_exactly(
    giftcard_order_settled_with_two_cards,
):
    """Proportional shares, with the remainder absorbed by the last card.

    30 and 10 of a 40 redemption, refunding 10, is 7.50 / 2.50 — and the
    parts must sum to the whole rather than to a cent less.
    """
    order, first, second = giftcard_order_settled_with_two_cards

    GiftCardService.credit_refund(order, Decimal("10.00"))

    rows = {
        row.gift_card_id: row.amount
        for row in GiftCardTransaction.objects.filter(
            order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
        )
    }
    assert rows[first.id] == Decimal("7.50")
    assert rows[second.id] == Decimal("2.50")
    assert sum(rows.values()) == Decimal("10.00")


def test_a_third_splits_without_losing_a_cent(
    giftcard_order_settled_with_two_cards,
):
    """The rounding case: 10/3 across two uneven cards."""
    order, _first, _second = giftcard_order_settled_with_two_cards

    GiftCardService.credit_refund(order, Decimal("3.33"))

    assert _credited(order) == Decimal("3.33")


def test_zero_credits_nothing(giftcard_order_settled_with_one_card):
    """What the Viva partial-reversal path sends when it cannot know.

    Under-crediting is visible and an operator can correct it;
    over-crediting is silent and cannot be undone.
    """
    order, _card = giftcard_order_settled_with_one_card

    GiftCardService.credit_refund(order, Decimal(0))

    assert _credited(order) == Decimal(0)
    assert not GiftCardTransaction.objects.filter(
        order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
    ).exists()


def test_the_signal_carries_the_amount_through_to_the_card(
    giftcard_order_settled_with_one_card,
):
    """End-to-end, and the one test that fails on the OLD behaviour.

    `OrderService.refund_order` has always sent `amount=` on
    `order_refunded`; the gift-card handler dropped it and credited the
    full redemption. This drives the real signal and asserts the card
    gets back what was refunded, not what it once paid.
    """
    from djmoney.money import Money

    from order.models.order import Order
    from order.signals import order_refunded

    order, _card = giftcard_order_settled_with_one_card

    order_refunded.send(
        sender=Order, order=order, amount=Money(Decimal("5.00"), "EUR")
    )

    assert _credited(order) == Decimal("5.00"), (
        "the refunded amount must reach the card; crediting the whole "
        "redemption for a partial refund creates money"
    )
