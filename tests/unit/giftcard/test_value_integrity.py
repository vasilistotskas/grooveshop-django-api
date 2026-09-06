"""Two ways gift-card value could be created or destroyed.

Both are pure arithmetic-and-lifecycle defects, reachable without any
concurrency, and neither had a test.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from djmoney.money import Money

from giftcard.factories import GiftCardFactory
from giftcard.models import (
    GiftCardStatus,
    GiftCardTransaction,
    GiftCardTransactionKind,
)
from giftcard.services import GiftCardError, GiftCardService

pytestmark = pytest.mark.django_db


class TestCurrencyIsNotConvertedAtOneToOne:
    """`CURRENCIES` is ("EUR", "USD") and `initial_value` is an
    admin-editable MoneyField, so a foreign-currency card is a few
    clicks away. The plan compared raw Decimals, so a $100 card
    extinguished EUR 100 of liability against roughly EUR 92 of
    instrument — value created on every such redemption.
    """

    def test_a_foreign_currency_card_is_refused(self, enable_gift_cards):
        card = GiftCardFactory(
            initial_value=Money(Decimal("100.00"), "USD"),
            status=GiftCardStatus.ACTIVE,
        )

        with pytest.raises(GiftCardError) as excinfo:
            GiftCardService.plan_redemption(
                [card.code], Money(Decimal("100.00"), "EUR")
            )

        assert excinfo.value.reason == "gift_card_currency_mismatch"

    def test_a_matching_currency_card_still_settles(self, enable_gift_cards):
        card = GiftCardFactory(
            initial_value=Money(Decimal("100.00"), "EUR"),
            status=GiftCardStatus.ACTIVE,
        )

        plan = GiftCardService.plan_redemption(
            [card.code], Money(Decimal("40.00"), "EUR")
        )

        assert plan.amount == Money(Decimal("40.00"), "EUR")


class TestARefundIsNotConfiscatedByTheExpirySweep:
    """A card can lapse while its value sits on an order refunded later.

    The credit landed on a card `plan_redemption` refuses and
    `expire_cards` reclaims on its next run, so the customer's refund was
    destroyed within a day — logged only as "Expired N gift cards", while
    the refund task reported success.
    """

    def _expired_card_with_a_redemption(self, order):
        card = GiftCardFactory(
            initial_value=Money(Decimal("30.00"), "EUR"),
            status=GiftCardStatus.ACTIVE,
            expires_at=timezone.now() - timedelta(days=2),
        )
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=Decimal("-30.00"),
            order=order,
            description="Redeemed",
        )
        return card

    def test_the_credit_survives_the_next_sweep(
        self, enable_gift_cards, giftcard_order_settled_with_one_card
    ):
        order, _unused = giftcard_order_settled_with_one_card
        card = self._expired_card_with_a_redemption(order)

        GiftCardService.credit_refund(order, None)
        card.refresh_from_db()
        assert card.balance.amount == Decimal("30.00")

        GiftCardService.expire_cards()

        card.refresh_from_db()
        assert card.balance.amount == Decimal("30.00"), (
            "the sweep confiscated a refund that had just been returned"
        )

    def test_the_card_becomes_spendable_again(
        self, enable_gift_cards, giftcard_order_settled_with_one_card
    ):
        order, _unused = giftcard_order_settled_with_one_card
        card = self._expired_card_with_a_redemption(order)

        GiftCardService.credit_refund(order, None)

        card.refresh_from_db()
        assert card.expires_at > timezone.now()
        assert card.is_redeemable

    def test_a_card_still_in_date_is_left_alone(
        self, enable_gift_cards, giftcard_order_settled_with_one_card
    ):
        """Only a LAPSED card gets a new window."""
        order, _unused = giftcard_order_settled_with_one_card
        card = self._expired_card_with_a_redemption(order)
        original = timezone.now() + timedelta(days=10)
        card.expires_at = original
        card.save(update_fields=["expires_at"])

        GiftCardService.credit_refund(order, None)

        card.refresh_from_db()
        assert card.expires_at == original
