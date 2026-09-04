"""Promotion + gift-card deductions must reach every charge site.

Mirrors ``test_loyalty_discount_is_charged.py`` — the deductions live
on the order row and ``calculate_order_total_amount()`` is the single
authority the charge sites, verification guard and webhooks read.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from order.factories.order import OrderFactory

pytestmark = pytest.mark.django_db


class TestPromotionDiscountReachesTheCharge:
    def test_amount_due_subtracts_the_promotion_discount(self):
        order = OrderFactory()
        total = order.total_price

        order.discount_amount = Money(Decimal("7.50"), total.currency)
        order.save(
            update_fields=["discount_amount", "discount_amount_currency"]
        )

        due = order.calculate_order_total_amount()
        assert due.amount == total.amount - Decimal("7.50")
        assert order.total_price.amount == total.amount

    def test_all_three_deductions_combine(self):
        order = OrderFactory()
        total = order.total_price

        order.discount_amount = Money(Decimal("5.00"), total.currency)
        order.loyalty_discount = Money(Decimal("2.00"), total.currency)
        order.gift_card_amount = Money(Decimal("3.00"), total.currency)
        order.save(
            update_fields=[
                "discount_amount",
                "discount_amount_currency",
                "loyalty_discount",
                "loyalty_discount_currency",
                "gift_card_amount",
                "gift_card_amount_currency",
            ]
        )

        assert order.calculate_order_total_amount().amount == max(
            total.amount - Decimal("10.00"), Decimal(0)
        )

    def test_deductions_never_produce_a_negative_charge(self):
        order = OrderFactory()
        total = order.total_price

        order.discount_amount = Money(
            total.amount + Decimal(50), total.currency
        )
        order.save(
            update_fields=["discount_amount", "discount_amount_currency"]
        )

        assert order.calculate_order_total_amount().amount == Decimal(0)

    def test_pricing_breakdown_reports_deductions(self):
        from order.serializers.order import OrderDetailSerializer

        order = OrderFactory()
        order.discount_amount = Money(Decimal("4.00"), "EUR")
        order.gift_card_amount = Money(Decimal("6.00"), "EUR")
        order.save(
            update_fields=[
                "discount_amount",
                "discount_amount_currency",
                "gift_card_amount",
                "gift_card_amount_currency",
            ]
        )

        breakdown = OrderDetailSerializer().get_pricing_breakdown(order)

        items = breakdown["items_subtotal"]
        shipping = breakdown["shipping_cost"]
        fee = breakdown["payment_method_fee"]
        assert breakdown["discount"] == Decimal("4.00")
        assert breakdown["gift_card_amount"] == Decimal("6.00")
        assert breakdown["grand_total"] == max(
            items + shipping + fee - Decimal("4.00"), 0
        )
        # No phantom outstanding balance: the gift-card portion counts
        # as settled.
        assert breakdown["remaining_amount"] == max(
            breakdown["grand_total"]
            - Decimal("6.00")
            - breakdown["paid_amount"],
            0,
        )
