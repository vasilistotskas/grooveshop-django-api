"""What a provider reports for a payment, for tests that book one."""

from __future__ import annotations

from decimal import Decimal

from djmoney.money import Money

#: What a provider says it captured for the payment under test.
CAPTURED = Money(Decimal("50.00"), "EUR")
