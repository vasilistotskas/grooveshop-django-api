"""A pay-way fee configured in another currency must not be re-labelled.

``calculate_payment_method_fee`` returned ``Money(pay_way.cost.amount,
order_value.currency)`` — the amount from one currency wearing the label
of another. That is not a conversion, it is a different sum: a 3.50 GBP
fee became 3.50 EUR.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock

import pytest
from djmoney.money import Money

from order.services import OrderService

pytestmark = pytest.mark.django_db


def _pay_way(cost, *, free_threshold=None):
    return Mock(id=1, cost=cost, free_threshold=free_threshold)


def test_a_matching_currency_charges_the_fee():
    fee = OrderService.calculate_payment_method_fee(
        _pay_way(Money(Decimal("3.50"), "EUR")),
        Money(Decimal("45.00"), "EUR"),
    )

    assert fee == Money(Decimal("3.50"), "EUR")


def test_a_mismatched_currency_charges_nothing():
    fee = OrderService.calculate_payment_method_fee(
        _pay_way(Money(Decimal("3.50"), "USD")),
        Money(Decimal("45.00"), "EUR"),
    )

    assert fee == Money(Decimal(0), "EUR"), (
        "the USD amount was re-labelled as EUR, charging a different sum"
    )


def test_the_free_threshold_still_applies():
    fee = OrderService.calculate_payment_method_fee(
        _pay_way(
            Money(Decimal("3.50"), "EUR"),
            free_threshold=Money(Decimal("40.00"), "EUR"),
        ),
        Money(Decimal("45.00"), "EUR"),
    )

    assert fee == Money(Decimal(0), "EUR")
