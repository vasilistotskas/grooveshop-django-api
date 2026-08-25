from decimal import Decimal

import factory
from djmoney.money import Money

from giftcard.enum import GiftCardSource, GiftCardTransactionKind
from giftcard.models import GiftCard, GiftCardPurchase, GiftCardTransaction


class GiftCardFactory(factory.django.DjangoModelFactory):
    code = factory.Sequence(lambda n: f"GC-TEST-{n:04d}-CARD")
    initial_value = Money(Decimal("50.00"), "EUR")
    source = GiftCardSource.ADMIN

    class Meta:
        model = GiftCard
        django_get_or_create = ("code",)
        skip_postgeneration_save = True

    @factory.post_generation
    def with_issue_transaction(self, create, extracted, **kwargs):
        """Seed the ISSUE ledger row (balance is ledger-derived)."""
        if not create or extracted is False:
            return
        GiftCardTransaction.objects.create(
            gift_card=self,
            kind=GiftCardTransactionKind.ISSUE,
            amount=Decimal(self.initial_value.amount),
            description="Issued (factory)",
        )


class GiftCardPurchaseFactory(factory.django.DjangoModelFactory):
    buyer_email = factory.Sequence(lambda n: f"buyer{n}@example.com")
    amount = Money(Decimal("50.00"), "EUR")
    recipient_email = factory.Sequence(lambda n: f"recipient{n}@example.com")

    class Meta:
        model = GiftCardPurchase
