import pytest
from decimal import Decimal
from djmoney.money import Money

from order.factories import OrderFactory
from order.models.item import OrderItem
from product.models import Product


class TestCurrencyConsistency:
    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "currency",
        ["EUR", "USD", "GBP", "JPY", "CHF"],
    )
    def test_order_with_consistent_currency_accepted(self, currency):
        """
        Test that orders with consistent currency across all fields are accepted.

        All Money fields should use the same currency.
        """
        # Create order with specific currency
        order = OrderFactory(
            shipping_price=Money(Decimal("10.00"), currency),
            num_order_items=0,
        )

        # Create order items with same currency
        product = Product.objects.create(
            sku=f"TEST-{order.id}-1",
            price=Money(Decimal("20.00"), currency),
            stock=100,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(Decimal("20.00"), currency),
            quantity=2,
        )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify all Money fields use same currency
        assert order.shipping_price.currency.code == currency
        assert order.total_price.currency.code == currency
        assert order.total_price_items.currency.code == currency
        assert order.total_price_extra.currency.code == currency
        assert order.items.first().price.currency.code == currency

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "order_currency,item_currency",
        [
            ("EUR", "USD"),
            ("USD", "GBP"),
            ("GBP", "EUR"),
            ("EUR", "JPY"),
            ("USD", "CHF"),
        ],
    )
    def test_order_with_mixed_currency_raises_error(
        self, order_currency, item_currency
    ):
        """
        Test that orders with mixed currencies raise ValueError.

        Mixed currencies should be rejected.
        """
        # Create order with one currency
        order = OrderFactory(
            shipping_price=Money(Decimal("10.00"), order_currency),
            num_order_items=0,
        )

        # Create order item with different currency
        product = Product.objects.create(
            sku=f"TEST-{order.id}-1",
            price=Money(Decimal("20.00"), item_currency),
            stock=100,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(Decimal("20.00"), item_currency),
            quantity=2,
        )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify that accessing total_price raises ValueError due to currency mismatch
        with pytest.raises(ValueError, match="different currencies"):
            _ = order.total_price

    @pytest.mark.django_db
    def test_order_with_multiple_items_same_currency(self):
        """
        Test that orders with multiple items in same currency work correctly.

        Multiple items with same currency should be accepted.
        """
        currency = "EUR"
        order = OrderFactory(
            shipping_price=Money(Decimal("5.00"), currency),
            num_order_items=0,
        )

        # Create multiple items with same currency
        for i in range(3):
            product = Product.objects.create(
                sku=f"TEST-{order.id}-{i}",
                price=Money(Decimal("10.00"), currency),
                stock=100,
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                price=Money(Decimal("10.00"), currency),
                quantity=1,
            )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify all items and totals use same currency
        assert order.total_price.currency.code == currency
        for item in order.items.all():
            assert item.price.currency.code == currency

    @pytest.mark.django_db
    def test_order_with_multiple_items_mixed_currency_raises_error(self):
        """
        Test that orders with multiple items in different currencies raise error.

        Multiple items with different currencies should be rejected when calculating total.

        Note: The error occurs when total_price tries to add items_total and shipping_price
        with different currencies, not when calculating items_total alone.
        """
        order = OrderFactory(
            shipping_price=Money(Decimal("5.00"), "EUR"),
            num_order_items=0,
        )

        # Create items with different currencies
        # The first item's currency will be used for total_price_items
        currencies = ["USD", "GBP", "JPY"]  # Different from shipping (EUR)
        for i, currency in enumerate(currencies):
            product = Product.objects.create(
                sku=f"TEST-{order.id}-{i}",
                price=Money(Decimal("10.00"), currency),
                stock=100,
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                price=Money(Decimal("10.00"), currency),
                quantity=1,
            )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # total_price_items will use first item's currency (USD)
        # But shipping_price is EUR, so total_price will raise ValueError
        with pytest.raises(ValueError, match="different currencies"):
            _ = order.total_price

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "currency",
        ["EUR", "USD", "GBP"],
    )
    def test_order_total_price_items_respects_currency(self, currency):
        """
        Test that total_price_items uses correct currency.

        Calculated totals should maintain currency consistency.
        """
        order = OrderFactory(
            shipping_price=Money(Decimal("0.00"), currency),
            num_order_items=0,
        )

        # Create items with same currency
        product = Product.objects.create(
            sku=f"TEST-{order.id}-1",
            price=Money(Decimal("10.00"), currency),
            stock=100,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(Decimal("10.00"), currency),
            quantity=2,
        )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify total_price_items uses correct currency
        assert order.total_price_items.currency.code == currency
        assert order.total_price_items.amount == Decimal("20.00")

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "currency",
        ["EUR", "USD", "GBP"],
    )
    def test_order_total_price_extra_respects_currency(self, currency):
        """
        Test that total_price_extra uses correct currency.

        Shipping price should maintain currency consistency.
        """
        shipping_amount = Decimal("15.00")
        order = OrderFactory(
            shipping_price=Money(shipping_amount, currency),
            num_order_items=0,
        )

        # Verify total_price_extra uses correct currency
        assert order.total_price_extra.currency.code == currency
        assert order.total_price_extra.amount == shipping_amount

    @pytest.mark.django_db
    def test_order_with_zero_shipping_maintains_currency(self):
        """
        Test that orders with zero shipping maintain currency consistency.

        Zero-value Money fields should still have correct currency.
        """
        currency = "EUR"
        order = OrderFactory(
            shipping_price=Money(Decimal("0.00"), currency),
            num_order_items=0,
        )

        # Create item
        product = Product.objects.create(
            sku=f"TEST-{order.id}-1",
            price=Money(Decimal("10.00"), currency),
            stock=100,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(Decimal("10.00"), currency),
            quantity=1,
        )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify currency consistency even with zero shipping
        assert order.shipping_price.currency.code == currency
        assert order.total_price.currency.code == currency
        assert order.total_price.amount == Decimal("10.00")

    @pytest.mark.django_db
    def test_order_calculate_total_amount_respects_currency(self):
        """
        Test that calculate_order_total_amount returns correct currency.

        Calculated methods should maintain currency consistency.
        """
        currency = "USD"
        order = OrderFactory(
            shipping_price=Money(Decimal("5.00"), currency),
            num_order_items=0,
        )

        # Create item
        product = Product.objects.create(
            sku=f"TEST-{order.id}-1",
            price=Money(Decimal("10.00"), currency),
            stock=100,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(Decimal("10.00"), currency),
            quantity=2,
        )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify method returns correct currency
        calculated_total = order.calculate_order_total_amount()
        assert calculated_total.currency.code == currency
        assert calculated_total.amount == Decimal("25.00")

    @pytest.mark.django_db
    def test_order_with_no_items_maintains_currency(self):
        """
        Test that orders with no items maintain currency from shipping.

        Currency should be consistent even without items.
        """
        currency = "GBP"
        order = OrderFactory(
            shipping_price=Money(Decimal("10.00"), currency),
            num_order_items=0,
        )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify currency consistency
        assert order.shipping_price.currency.code == currency
        assert order.total_price.currency.code == currency
        assert order.total_price_extra.currency.code == currency

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "currencies",
        [
            ["EUR", "EUR", "EUR"],
            ["USD", "USD", "USD"],
            ["GBP", "GBP", "GBP"],
        ],
    )
    def test_order_with_multiple_items_consistent_currency(self, currencies):
        """
        Test that orders with multiple items in consistent currency work.

        All items should use the same currency.
        """
        currency = currencies[0]  # All same
        order = OrderFactory(
            shipping_price=Money(Decimal("5.00"), currency),
            num_order_items=0,
        )

        # Create multiple items with same currency
        for i, curr in enumerate(currencies):
            product = Product.objects.create(
                sku=f"TEST-{order.id}-{i}",
                price=Money(Decimal("10.00"), curr),
                stock=100,
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                price=Money(Decimal("10.00"), curr),
                quantity=1,
            )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify all use same currency
        assert order.total_price.currency.code == currency
        expected_total = Decimal("10.00") * len(currencies) + Decimal("5.00")
        assert order.total_price.amount == expected_total
