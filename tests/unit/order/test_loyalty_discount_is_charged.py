"""The amount charged must equal the amount the shopper was shown.

A loyalty redemption used to land ONLY on ``paid_amount`` while every
online charge site read the undiscounted ``total_price``:

- the checkout sidebar showed items + shipping + fee − discount;
- ``paid_amount`` was written with the discount applied;
- the points were burnt;
- Stripe and Viva were both handed ``total_price`` — the full amount;
- the Viva webhook's amount guard compared the captured amount against
  the same undiscounted figure, so it ENDORSED the overcharge instead
  of catching it.

Cash on delivery disagreed and collected the discounted figure
(``shipping_acs`` sets ``cod_amount`` from ``paid_amount``), and that
internal contradiction is what makes this a defect rather than a
pricing choice.

The discount is now persisted on the order and
``calculate_order_total_amount()`` is the single authority every charge
site, verification and webhook guard reads.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from order.factories.order import OrderFactory


@pytest.mark.django_db
class TestLoyaltyDiscountReachesTheCharge:
    def test_amount_due_subtracts_the_discount(self):
        order = OrderFactory()
        total = order.total_price

        order.loyalty_discount = Money(Decimal("5.00"), total.currency)
        order.save(
            update_fields=["loyalty_discount", "loyalty_discount_currency"]
        )

        due = order.calculate_order_total_amount()
        assert due.amount == total.amount - Decimal("5.00")
        # The raw total stays undiscounted so the discount can be shown
        # as its own line rather than being folded into the subtotal.
        assert order.total_price.amount == total.amount

    def test_no_discount_leaves_the_total_untouched(self):
        order = OrderFactory()
        assert (
            order.calculate_order_total_amount().amount
            == order.total_price.amount
        )

    def test_discount_never_produces_a_negative_charge(self):
        order = OrderFactory()
        total = order.total_price

        order.loyalty_discount = Money(
            total.amount + Decimal("100.00"), total.currency
        )
        order.save(
            update_fields=["loyalty_discount", "loyalty_discount_currency"]
        )

        assert order.calculate_order_total_amount().amount == Decimal("0")

    def test_paid_amount_matches_the_amount_due(self):
        """``mark_as_paid`` must not restore the undiscounted total."""
        order = OrderFactory(paid_amount=Money(0, "EUR"))
        order.loyalty_discount = Money(Decimal("5.00"), "EUR")
        order.save(
            update_fields=["loyalty_discount", "loyalty_discount_currency"]
        )

        order.mark_as_paid(payment_id="pi_test", payment_method="card")

        order.refresh_from_db()
        assert order.paid_amount.amount == (
            order.total_price.amount - Decimal("5.00")
        )
