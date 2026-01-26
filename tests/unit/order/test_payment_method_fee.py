"""
Unit tests for payment method fee calculation.

Tests the calculate_payment_method_fee method in OrderService to ensure
it correctly calculates fees based on PayWay configuration and free thresholds.
"""

import pytest
from decimal import Decimal
from django.conf import settings
from djmoney.money import Money

from order.services import OrderService
from pay_way.factories import PayWayFactory


@pytest.mark.django_db
class TestCalculatePaymentMethodFee:
    """Test OrderService.calculate_payment_method_fee method."""

    def test_no_payment_method_returns_zero(self):
        """Test that None pay_way returns zero fee."""
        order_value = Money(Decimal("50.00"), settings.DEFAULT_CURRENCY)

        fee = OrderService.calculate_payment_method_fee(
            pay_way=None,
            order_value=order_value,
        )

        assert fee.amount == Decimal("0.00")
        assert fee.currency.code == settings.DEFAULT_CURRENCY

    def test_payment_method_with_no_cost_returns_zero(self):
        """Test that pay_way with cost=0 returns zero fee."""
        pay_way = PayWayFactory(cost=0)
        order_value = Money(Decimal("50.00"), settings.DEFAULT_CURRENCY)

        fee = OrderService.calculate_payment_method_fee(
            pay_way=pay_way,
            order_value=order_value,
        )

        assert fee.amount == Decimal("0.00")

    def test_payment_method_with_cost_below_threshold(self):
        """Test that pay_way returns cost when order below free threshold."""
        pay_way = PayWayFactory(
            cost=Decimal("3.50"),
            free_threshold=Decimal("100.00"),
        )
        order_value = Money(Decimal("50.00"), settings.DEFAULT_CURRENCY)

        fee = OrderService.calculate_payment_method_fee(
            pay_way=pay_way,
            order_value=order_value,
        )

        assert fee.amount == Decimal("3.50")
        assert fee.currency.code == settings.DEFAULT_CURRENCY

    def test_payment_method_with_cost_above_threshold(self):
        """Test that pay_way returns zero when order meets free threshold."""
        pay_way = PayWayFactory(
            cost=Decimal("3.50"),
            free_threshold=Decimal("50.00"),
        )
        order_value = Money(Decimal("100.00"), settings.DEFAULT_CURRENCY)

        fee = OrderService.calculate_payment_method_fee(
            pay_way=pay_way,
            order_value=order_value,
        )

        assert fee.amount == Decimal("0.00")

    def test_payment_method_with_cost_exactly_at_threshold(self):
        """Test that pay_way returns zero when order exactly meets threshold."""
        pay_way = PayWayFactory(
            cost=Decimal("3.50"),
            free_threshold=Decimal("50.00"),
        )
        order_value = Money(Decimal("50.00"), settings.DEFAULT_CURRENCY)

        fee = OrderService.calculate_payment_method_fee(
            pay_way=pay_way,
            order_value=order_value,
        )

        assert fee.amount == Decimal("0.00")

    def test_payment_method_with_no_threshold(self):
        """Test that pay_way with no threshold always charges fee."""
        pay_way = PayWayFactory(
            cost=Decimal("3.50"),
            free_threshold=0,
        )
        order_value = Money(Decimal("1000.00"), settings.DEFAULT_CURRENCY)

        fee = OrderService.calculate_payment_method_fee(
            pay_way=pay_way,
            order_value=order_value,
        )

        assert fee.amount == Decimal("3.50")

    @pytest.mark.parametrize(
        "order_amount,expected_fee",
        [
            (Decimal("10.00"), Decimal("3.50")),  # Below threshold
            (Decimal("49.99"), Decimal("3.50")),  # Just below threshold
            (Decimal("50.00"), Decimal("0.00")),  # Exactly at threshold
            (Decimal("50.01"), Decimal("0.00")),  # Just above threshold
            (Decimal("100.00"), Decimal("0.00")),  # Well above threshold
        ],
    )
    def test_payment_method_fee_with_various_amounts(
        self, order_amount, expected_fee
    ):
        """Test payment fee calculation with various order amounts."""
        pay_way = PayWayFactory(
            cost=Decimal("3.50"),
            free_threshold=Decimal("50.00"),
        )
        order_value = Money(order_amount, settings.DEFAULT_CURRENCY)

        fee = OrderService.calculate_payment_method_fee(
            pay_way=pay_way,
            order_value=order_value,
        )

        assert fee.amount == expected_fee

    def test_payment_method_fee_currency_matches_order(self):
        """Test that payment fee currency matches order currency."""
        pay_way = PayWayFactory(cost=Decimal("3.50"))
        order_value = Money(Decimal("50.00"), "EUR")

        fee = OrderService.calculate_payment_method_fee(
            pay_way=pay_way,
            order_value=order_value,
        )

        assert fee.currency.code == "EUR"

    def test_real_world_cod_scenario(self):
        """
        Test real-world Cash on Delivery scenario.

        Scenario:
        - Items: 93,00 €
        - Shipping: 3,00 €
        - Subtotal: 96,00 €
        - COD fee: 3,50 € (if subtotal < 100 €)
        - Total: 99,50 €
        """
        pay_way = PayWayFactory(
            cost=Decimal("3.50"),
            free_threshold=Decimal("100.00"),
        )

        # Order subtotal (items + shipping)
        order_subtotal = Money(Decimal("96.00"), "EUR")

        fee = OrderService.calculate_payment_method_fee(
            pay_way=pay_way,
            order_value=order_subtotal,
        )

        assert fee.amount == Decimal("3.50")

        # Verify total
        total = order_subtotal.amount + fee.amount
        assert total == Decimal("99.50")
