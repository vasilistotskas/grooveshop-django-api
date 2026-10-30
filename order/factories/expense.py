from datetime import date
from decimal import Decimal

import factory
from django.conf import settings
from djmoney.money import Money

from order.enum.expense import ExpenseCategory
from order.models.expense import Expense


class ExpenseFactory(factory.django.DjangoModelFactory):
    """A 100.00 net shipping invoice (24.00 VAT) dated 2026-10-05 unless told
    otherwise."""

    date = date(2026, 10, 5)
    category = ExpenseCategory.SHIPPING
    description = factory.Sequence(lambda n: f"Invoice {n}")
    amount_net = Money(Decimal("100.00"), settings.DEFAULT_CURRENCY)
    vat_amount = Money(Decimal("24.00"), settings.DEFAULT_CURRENCY)

    class Meta:
        model = Expense
