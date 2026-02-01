import pytest
from decimal import Decimal
from djmoney.money import Money

from order.factories import OrderFactory
from order.models.item import OrderItem
from product.models import Product


class TestOrderTotalCalculation:
    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "items_data,shipping_amount,expected_total",
        [
            # Single item, no shipping
            (
                [{"price": Decimal("10.00"), "quantity": 1}],
                Decimal("0.00"),
                Decimal("10.00"),
            ),
            # Single item with shipping
            (
                [{"price": Decimal("10.00"), "quantity": 1}],
                Decimal("5.00"),
                Decimal("15.00"),
            ),
            # Multiple items, no shipping
            (
                [
                    {"price": Decimal("10.00"), "quantity": 2},
                    {"price": Decimal("15.00"), "quantity": 1},
                ],
                Decimal("0.00"),
                Decimal("35.00"),  # (10*2) + (15*1) = 35
            ),
            # Multiple items with shipping
            (
                [
                    {"price": Decimal("10.00"), "quantity": 2},
                    {"price": Decimal("15.00"), "quantity": 1},
                ],
                Decimal("5.00"),
                Decimal("40.00"),  # (10*2) + (15*1) + 5 = 40
            ),
            # Multiple items with different quantities
            (
                [
                    {"price": Decimal("10.00"), "quantity": 3},
                    {"price": Decimal("20.00"), "quantity": 2},
                    {"price": Decimal("5.00"), "quantity": 5},
                ],
                Decimal("10.00"),
                Decimal("105.00"),  # (10*3) + (20*2) + (5*5) + 10 = 105
            ),
            # Decimal prices
            (
                [
                    {"price": Decimal("9.99"), "quantity": 1},
                    {"price": Decimal("19.99"), "quantity": 2},
                ],
                Decimal("4.99"),
                Decimal("54.96"),  # 9.99 + (19.99*2) + 4.99 = 54.96
            ),
            # Large quantities
            (
                [
                    {"price": Decimal("1.00"), "quantity": 100},
                    {"price": Decimal("2.50"), "quantity": 50},
                ],
                Decimal("15.00"),
                Decimal("240.00"),  # (1*100) + (2.5*50) + 15 = 240
            ),
            # Zero shipping
            (
                [
                    {"price": Decimal("25.00"), "quantity": 2},
                ],
                Decimal("0.00"),
                Decimal("50.00"),
            ),
            # High shipping cost
            (
                [
                    {"price": Decimal("10.00"), "quantity": 1},
                ],
                Decimal("50.00"),
                Decimal("60.00"),
            ),
        ],
    )
    def test_order_total_calculation_correct(
        self, items_data, shipping_amount, expected_total
    ):
        """
        Test that order total is calculated correctly.

        total_price = sum(item.price * item.quantity) + shipping_price
        """
        # Create order with explicit shipping price (no factory randomness)
        order = OrderFactory(
            shipping_price=Money(shipping_amount, "EUR"),
            num_order_items=0,  # Don't create random items
        )

        # Create order items with explicit prices (bypassing product factory VAT issues)
        for item_data in items_data:
            # Create a minimal product without using factory
            product = Product.objects.create(
                sku=f"TEST-{order.id}-{len(order.items.all())}",
                price=Money(
                    Decimal("1.00"), "EUR"
                ),  # Doesn't matter, we use item price
                stock=100,
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                price=Money(item_data["price"], "EUR"),
                quantity=item_data["quantity"],
            )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify total calculation
        assert order.total_price.amount == expected_total
        assert order.total_price.currency.code == "EUR"

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "currency",
        ["EUR", "USD", "GBP"],
    )
    def test_order_total_respects_currency(self, currency):
        """
        Test that order total uses the correct currency.

        All Money fields should use the same currency.
        """
        # Create order with specific currency (no random items)
        order = OrderFactory(
            shipping_price=Money(Decimal("5.00"), currency),
            num_order_items=0,
        )

        # Create order items with same currency
        product = Product.objects.create(
            sku=f"TEST-{order.id}-1",
            price=Money(Decimal("1.00"), currency),
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

        # Verify currency consistency
        assert order.total_price.currency.code == currency
        assert order.shipping_price.currency.code == currency
        assert order.items.first().price.currency.code == currency

    @pytest.mark.django_db
    def test_order_total_with_no_items(self):
        """
        Test that order total equals shipping price when no items exist.

        Edge case: Order with no items should have total = shipping_price.
        """
        order = OrderFactory(
            shipping_price=Money(Decimal("5.00"), "EUR"),
            num_order_items=0,  # Explicitly no items
        )

        # No items created

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Total should equal shipping price
        assert order.total_price.amount == Decimal("5.00")
        assert order.total_price.currency.code == "EUR"

    @pytest.mark.django_db
    def test_order_total_items_calculation(self):
        """
        Test that total_price_items is calculated correctly.

        total_price_items = sum(item.price * item.quantity)
        """
        order = OrderFactory(
            shipping_price=Money(Decimal("0.00"), "EUR"),
            num_order_items=0,
        )

        # Create multiple items
        items_data = [
            {"price": Decimal("10.00"), "quantity": 2},
            {"price": Decimal("15.00"), "quantity": 3},
            {"price": Decimal("5.00"), "quantity": 1},
        ]

        for idx, item_data in enumerate(items_data):
            product = Product.objects.create(
                sku=f"TEST-{order.id}-{idx}",
                price=Money(Decimal("1.00"), "EUR"),
                stock=100,
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                price=Money(item_data["price"], "EUR"),
                quantity=item_data["quantity"],
            )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Calculate expected total
        expected_items_total = sum(
            item["price"] * item["quantity"] for item in items_data
        )

        # Verify items total
        assert order.total_price_items.amount == expected_items_total
        assert order.total_price_items.currency.code == "EUR"

    @pytest.mark.django_db
    def test_order_total_extra_equals_shipping(self):
        """
        Test that total_price_extra equals shipping_price.

        total_price_extra = shipping_price
        """
        shipping_amount = Decimal("12.50")
        order = OrderFactory(
            shipping_price=Money(shipping_amount, "EUR"),
            num_order_items=0,
        )

        # Verify extras total equals shipping
        assert order.total_price_extra.amount == shipping_amount
        assert order.total_price_extra.currency.code == "EUR"

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "items_data,shipping_amount",
        [
            # Various combinations to test calculation consistency
            (
                [{"price": Decimal("100.00"), "quantity": 1}],
                Decimal("10.00"),
            ),
            (
                [
                    {"price": Decimal("50.00"), "quantity": 2},
                    {"price": Decimal("25.00"), "quantity": 4},
                ],
                Decimal("15.00"),
            ),
            (
                [
                    {"price": Decimal("9.99"), "quantity": 3},
                    {"price": Decimal("14.99"), "quantity": 2},
                    {"price": Decimal("4.99"), "quantity": 5},
                ],
                Decimal("7.99"),
            ),
        ],
    )
    def test_order_total_equals_items_plus_shipping(
        self, items_data, shipping_amount
    ):
        """
        Test that total_price = total_price_items + total_price_extra.

        Verify the relationship between total components.
        """
        order = OrderFactory(
            shipping_price=Money(shipping_amount, "EUR"),
            num_order_items=0,
        )

        # Create order items
        for idx, item_data in enumerate(items_data):
            product = Product.objects.create(
                sku=f"TEST-{order.id}-{idx}",
                price=Money(Decimal("1.00"), "EUR"),
                stock=100,
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                price=Money(item_data["price"], "EUR"),
                quantity=item_data["quantity"],
            )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify relationship
        expected_total = (
            order.total_price_items.amount + order.total_price_extra.amount
        )
        assert order.total_price.amount == expected_total

    @pytest.mark.django_db
    def test_calculate_order_total_amount_returns_total_price(self):
        """
        Test that calculate_order_total_amount returns total_price.

        The method should return the calculated total.
        """
        order = OrderFactory(
            shipping_price=Money(Decimal("5.00"), "EUR"),
            num_order_items=0,
        )

        product = Product.objects.create(
            sku=f"TEST-{order.id}-1",
            price=Money(Decimal("1.00"), "EUR"),
            stock=100,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(Decimal("10.00"), "EUR"),
            quantity=2,
        )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify method returns total_price
        calculated_total = order.calculate_order_total_amount()
        assert calculated_total.amount == order.total_price.amount
        assert calculated_total.currency == order.total_price.currency

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "item_price,quantity,shipping,expected",
        [
            # Test precision with decimal calculations
            (Decimal("0.01"), 1, Decimal("0.01"), Decimal("0.02")),
            (Decimal("0.99"), 3, Decimal("0.03"), Decimal("3.00")),
            (Decimal("1.11"), 9, Decimal("1.11"), Decimal("11.10")),
            (Decimal("99.99"), 1, Decimal("0.01"), Decimal("100.00")),
        ],
    )
    def test_order_total_decimal_precision(
        self, item_price, quantity, shipping, expected
    ):
        """
        Test that order total maintains correct decimal precision.

        Decimal calculations should be precise.
        """
        order = OrderFactory(
            shipping_price=Money(shipping, "EUR"),
            num_order_items=0,
        )

        product = Product.objects.create(
            sku=f"TEST-{order.id}-1",
            price=Money(Decimal("1.00"), "EUR"),
            stock=100,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(item_price, "EUR"),
            quantity=quantity,
        )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Verify precision
        assert order.total_price.amount == expected

    @pytest.mark.django_db
    def test_order_total_with_zero_price_items(self):
        """
        Test order total calculation with zero-price items.

        Edge case: Items with zero price should not affect calculation incorrectly.
        """
        order = OrderFactory(
            shipping_price=Money(Decimal("5.00"), "EUR"),
            num_order_items=0,
        )

        # Create items with zero price
        product1 = Product.objects.create(
            sku=f"TEST-{order.id}-1",
            price=Money(Decimal("1.00"), "EUR"),
            stock=100,
        )
        OrderItem.objects.create(
            order=order,
            product=product1,
            price=Money(Decimal("0.00"), "EUR"),
            quantity=5,
        )

        # Create item with normal price
        product2 = Product.objects.create(
            sku=f"TEST-{order.id}-2",
            price=Money(Decimal("1.00"), "EUR"),
            stock=100,
        )
        OrderItem.objects.create(
            order=order,
            product=product2,
            price=Money(Decimal("10.00"), "EUR"),
            quantity=1,
        )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Total should be 0 + 10 + 5 = 15
        assert order.total_price.amount == Decimal("15.00")

    @pytest.mark.django_db
    def test_order_total_with_large_numbers(self):
        """
        Test order total calculation with large numbers.

        Should handle large order values correctly.
        """
        order = OrderFactory(
            shipping_price=Money(Decimal("100.00"), "EUR"),
            num_order_items=0,
        )

        # Create item with large price and quantity
        product = Product.objects.create(
            sku=f"TEST-{order.id}-1",
            price=Money(Decimal("1.00"), "EUR"),
            stock=1000,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(Decimal("999.99"), "EUR"),
            quantity=100,
        )

        # Refresh to get calculated properties
        order.refresh_from_db()

        # Total should be (999.99 * 100) + 100 = 100099.00
        expected = Decimal("999.99") * 100 + Decimal("100.00")
        assert order.total_price.amount == expected
