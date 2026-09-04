"""GiftCardService: ledger semantics, planning math, purchase flow."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone
from djmoney.money import Money

from giftcard.enum import (
    GiftCardPurchaseStatus,
    GiftCardStatus,
    GiftCardTransactionKind,
)
from giftcard.factories import GiftCardFactory, GiftCardPurchaseFactory
from giftcard.models import GiftCard, GiftCardTransaction
from giftcard.services import GiftCardError, GiftCardService

pytestmark = pytest.mark.django_db


class TestIssue:
    def test_issue_creates_card_with_ledger_row(self, enable_gift_cards):
        card = GiftCardService.issue(Money(Decimal(75), "EUR"))

        assert card.code.startswith("GC-")
        assert len(card.code) == 17  # GC- + 3x4 chars + 2 dashes
        assert card.balance.amount == Decimal(75)
        assert card.expires_at is not None
        assert card.transactions.get().kind == GiftCardTransactionKind.ISSUE

    def test_issue_rejects_non_positive_value(self, enable_gift_cards):
        with pytest.raises(GiftCardError):
            GiftCardService.issue(Money(Decimal(0), "EUR"))


class TestBalance:
    def test_balance_is_ledger_derived(self, enable_gift_cards):
        card = GiftCardFactory(initial_value=Money(Decimal(50), "EUR"))
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=Decimal(-20),
        )
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REFUND_CREDIT,
            amount=Decimal(5),
        )

        assert card.balance.amount == Decimal(35)

    def test_balance_floors_at_zero(self, enable_gift_cards):
        card = GiftCardFactory(with_issue_transaction=False)
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.ADJUST,
            amount=Decimal(-10),
        )

        assert card.balance.amount == Decimal(0)


class TestPlanRedemption:
    def test_partial_coverage(self, enable_gift_cards):
        GiftCardFactory(
            code="GC-AAAA-AAAA-AAAA",
            initial_value=Money(Decimal(30), "EUR"),
        )

        plan = GiftCardService.plan_redemption(
            ["gc-aaaa-aaaa-aaaa"], Money(Decimal(100), "EUR")
        )

        assert plan.amount.amount == Decimal(30)

    def test_smallest_balance_first(self, enable_gift_cards):
        small = GiftCardFactory(
            code="GC-SMLL-0000-0001",
            initial_value=Money(Decimal(10), "EUR"),
        )
        large = GiftCardFactory(
            code="GC-LRGE-0000-0002",
            initial_value=Money(Decimal(100), "EUR"),
        )

        plan = GiftCardService.plan_redemption(
            [large.code, small.code], Money(Decimal(40), "EUR")
        )

        assert plan.amount.amount == Decimal(40)
        contributions = {card.code: take for card, take in plan.per_card}
        assert contributions[small.code] == Decimal(10)
        assert contributions[large.code] == Decimal(30)

    def test_full_coverage_leaves_zero_remainder(self, enable_gift_cards):
        card = GiftCardFactory(initial_value=Money(Decimal(100), "EUR"))

        plan = GiftCardService.plan_redemption(
            [card.code], Money(Decimal(80), "EUR")
        )

        assert plan.amount.amount == Decimal(80)

    def test_provider_minimum_floor(self, enable_gift_cards):
        # Card covers all but 0.30 EUR — under Stripe's 0.50 minimum,
        # so the plan trims itself to leave exactly 0.50 chargeable.
        card = GiftCardFactory(initial_value=Money(Decimal("99.70"), "EUR"))

        plan = GiftCardService.plan_redemption(
            [card.code], Money(Decimal(100), "EUR")
        )

        assert plan.amount.amount == Decimal("99.50")

    def test_floor_trim_pop_keeps_plan_and_ledger_in_lockstep(
        self, enable_gift_cards
    ):
        # Two small cards fall 0.10 short of a 0.60 due — the trim
        # must POP the last card and keep trimming from the previous
        # one, so plan.amount always equals the per-card sum that the
        # ledger rows will debit (regression: the single-pass trim
        # debited 0.15 while crediting the order only 0.10).
        card_small = GiftCardFactory(
            initial_value=Money(Decimal("0.15"), "EUR")
        )
        card_big = GiftCardFactory(initial_value=Money(Decimal("0.35"), "EUR"))

        plan = GiftCardService.plan_redemption(
            [card_small.code, card_big.code], Money(Decimal("0.60"), "EUR")
        )

        per_card_sum = sum(
            (take for _, take in plan.per_card), start=Decimal(0)
        )
        assert plan.amount.amount == per_card_sum == Decimal("0.10")
        # Provider remainder respects the 0.50 floor exactly.
        assert Decimal("0.60") - plan.amount.amount == Decimal("0.50")

    def test_floor_trim_can_empty_the_plan(self, enable_gift_cards):
        # A single tiny card against a due just above it: any positive
        # redemption would leave a sub-minimum provider charge, so the
        # plan empties and the provider charges the full due.
        card = GiftCardFactory(initial_value=Money(Decimal("0.05"), "EUR"))

        plan = GiftCardService.plan_redemption(
            [card.code], Money(Decimal("0.40"), "EUR")
        )

        assert plan.per_card == []
        assert plan.amount.amount == Decimal(0)

    def test_unknown_code_rejected(self, enable_gift_cards):
        with pytest.raises(GiftCardError) as excinfo:
            GiftCardService.plan_redemption(
                ["GC-NOPE-NOPE-NOPE"], Money(Decimal(50), "EUR")
            )
        assert excinfo.value.reason == "gift_card_invalid"

    def test_disabled_card_rejected(self, enable_gift_cards):
        card = GiftCardFactory(status=GiftCardStatus.DISABLED)

        with pytest.raises(GiftCardError) as excinfo:
            GiftCardService.plan_redemption(
                [card.code], Money(Decimal(50), "EUR")
            )
        assert excinfo.value.reason == "gift_card_not_redeemable"

    def test_expired_card_rejected(self, enable_gift_cards):
        card = GiftCardFactory(
            expires_at=timezone.now() - timezone.timedelta(days=1)
        )

        with pytest.raises(GiftCardError):
            GiftCardService.plan_redemption(
                [card.code], Money(Decimal(50), "EUR")
            )

    def test_too_many_cards_rejected(self, enable_gift_cards):
        cards = [GiftCardFactory() for _ in range(4)]

        with pytest.raises(GiftCardError) as excinfo:
            GiftCardService.plan_redemption(
                [card.code for card in cards],
                Money(Decimal(500), "EUR"),
            )
        assert excinfo.value.reason == "gift_card_too_many"

    def test_disabled_feature_rejects(self):
        card = GiftCardFactory()

        with pytest.raises(GiftCardError):
            GiftCardService.plan_redemption(
                [card.code], Money(Decimal(50), "EUR")
            )


class TestExpiry:
    def test_expire_writes_min_clamped_row(self, enable_gift_cards):
        card = GiftCardFactory(
            initial_value=Money(Decimal(50), "EUR"),
            expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=Decimal(-30),
        )

        expired = GiftCardService.expire_cards()

        assert expired == 1
        assert card.balance.amount == Decimal(0)
        expire_row = card.transactions.get(kind=GiftCardTransactionKind.EXPIRE)
        assert expire_row.amount == Decimal(-20)

    def test_expire_skips_empty_and_unexpired(self, enable_gift_cards):
        GiftCardFactory()  # not expired
        empty = GiftCardFactory(
            with_issue_transaction=False,
            expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        assert GiftCardService.expire_cards() == 0
        assert empty.transactions.count() == 0


class TestCompletePurchase:
    def test_completes_once(self, enable_gift_cards):
        purchase = GiftCardPurchaseFactory(amount=Money(Decimal(60), "EUR"))

        card = GiftCardService.complete_purchase(purchase, "pi_123")
        again = GiftCardService.complete_purchase(purchase, "pi_123")

        purchase.refresh_from_db()
        assert purchase.status == GiftCardPurchaseStatus.PAID
        assert card is not None
        assert again == card
        assert GiftCard.objects.filter(purchase=purchase).count() == 1
        assert card.balance.amount == Decimal(60)
        assert card.recipient_email == purchase.recipient_email


class TestRefundCredit:
    def test_credit_refund_is_idempotent(self, enable_gift_cards):
        from order.factories.order import OrderFactory

        order = OrderFactory()
        card = GiftCardFactory(initial_value=Money(Decimal(50), "EUR"))
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=Decimal(-20),
            order=order,
        )

        first = GiftCardService.credit_refund(order)
        second = GiftCardService.credit_refund(order)

        assert first == Decimal(20)
        assert second == Decimal(0)
        assert card.balance.amount == Decimal(50)

    def test_duplicate_refund_credit_blocked_at_db_level(
        self, enable_gift_cards
    ):
        # The check-then-act in credit_refund cannot stop two RACING
        # tasks by itself — the partial unique constraint must. A
        # direct duplicate insert (what the race loser would attempt)
        # has to die in the database.
        from django.db import IntegrityError, transaction

        from order.factories.order import OrderFactory

        order = OrderFactory()
        card = GiftCardFactory(initial_value=Money(Decimal(50), "EUR"))
        for _ in range(2):
            try:
                with transaction.atomic():
                    GiftCardTransaction.objects.create(
                        gift_card=card,
                        kind=GiftCardTransactionKind.REFUND_CREDIT,
                        amount=Decimal(20),
                        order=order,
                    )
            except IntegrityError:
                duplicate_blocked = True
            else:
                duplicate_blocked = False

        assert duplicate_blocked
        assert (
            GiftCardTransaction.objects.filter(
                order=order,
                kind=GiftCardTransactionKind.REFUND_CREDIT,
            ).count()
            == 1
        )
