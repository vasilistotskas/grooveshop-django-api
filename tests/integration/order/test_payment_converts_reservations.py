import pytest
from unittest.mock import patch, Mock

from order.stock import StockManager
from order.services import OrderService
from order.models import StockLog, Order
from order.enum.status import OrderStatus, PaymentStatus
from product.factories import ProductFactory
from cart.factories import CartFactory, CartItemFactory
from pay_way.factories import PayWayFactory
from user.factories import UserAccountFactory
from country.factories import CountryFactory


@pytest.mark.django_db
class TestPaymentConfirmationConvertsReservations:
    """
    Payment Confirmation Converts Reservations.

    This test suite validates that when payment is confirmed and an order is
    created from a cart with stock reservations, the reservations are properly
    converted to stock decrements.
    """

    @pytest.fixture
    def mock_payment_validation(self):
        """Mock payment provider validation to avoid external API calls."""
        with patch("order.payment.get_payment_provider") as mock_provider:
            mock_instance = Mock()
            mock_instance.get_payment_status.return_value = (
                PaymentStatus.COMPLETED,
                {"status": "succeeded", "amount": 10000, "currency": "usd"},
            )
            mock_provider.return_value = mock_instance
            yield mock_provider

    @pytest.fixture
    def country(self):
        """Create a test country for foreign key constraints."""
        return CountryFactory(alpha_2="US", name="United States")

    @pytest.mark.parametrize(
        "num_products,quantities,initial_stocks,description",
        [
            # Single product scenarios
            (1, [5], [100], "Single product, small quantity"),
            (1, [50], [100], "Single product, half stock"),
            (1, [1], [10], "Single product, minimal quantity"),
            # Multiple products scenarios
            (2, [10, 20], [100, 100], "Two products, different quantities"),
            (
                3,
                [5, 10, 15],
                [50, 50, 50],
                "Three products, ascending quantities",
            ),
            (2, [25, 25], [50, 50], "Two products, equal quantities"),
            # Edge cases
            (3, [1, 1, 1], [10, 10, 10], "Three products, minimal quantities"),
        ],
    )
    def test_payment_success_converts_reservations_to_stock_decrements(
        self,
        mock_payment_validation,
        country,
        num_products,
        quantities,
        initial_stocks,
        description,
    ):
        """
        Test that payment confirmation converts reservations to stock decrements.

        This test verifies that when an order is created from a cart with stock
        reservations (simulating payment confirmation), the reservations are:
        1. Marked as consumed
        2. Linked to the order
        3. Converted to actual stock decrements

        Args:
            num_products: Number of products in the order
            quantities: List of quantities for each product
            initial_stocks: List of initial stock levels for each product
            description: Human-readable description of the test case

        Test Requirements:
        - Test: Payment success converts reservations to stock decrements
        - Use @pytest.mark.parametrize with various order/reservation scenarios
        - Verify: Reservations marked consumed
        - Verify: Stock decremented correctly
        """
        # Setup: Create products with specified stock levels
        products = [
            ProductFactory(stock=initial_stocks[i]) for i in range(num_products)
        ]

        # Create a cart with items
        user = UserAccountFactory()
        cart = CartFactory(user=user)
        cart_items = []
        for i in range(num_products):
            cart_item = CartItemFactory(
                cart=cart, product=products[i], quantity=quantities[i]
            )
            cart_items.append(cart_item)

        # Create stock reservations for the cart
        reservations = []
        for i in range(num_products):
            reservation = StockManager.reserve_stock(
                product_id=products[i].id,
                quantity=quantities[i],
                session_id=str(cart.uuid),
                user_id=user.id,
            )
            reservations.append(reservation)

        # Verify initial state: reservations exist and are not consumed
        for i, reservation in enumerate(reservations):
            assert reservation.consumed is False, (
                f"Reservation {i} should not be consumed initially for {description}"
            )
            assert reservation.product_id == products[i].id
            assert reservation.quantity == quantities[i]

        # Verify initial stock levels
        for i, product in enumerate(products):
            product.refresh_from_db()
            assert product.stock == initial_stocks[i], (
                f"Product {i} should have initial stock {initial_stocks[i]} for {description}"
            )

        # Create payment intent ID (simulating successful payment)
        payment_intent_id = (
            f"pi_test_{description.replace(' ', '_').replace(',', '')}"
        )

        # Create pay way with stripe provider
        pay_way = PayWayFactory(provider_code="stripe")

        # Prepare shipping address with valid country
        shipping_address = {
            "first_name": "Test",
            "last_name": "User",
            "email": "test@example.com",
            "street": "123 Test St",
            "street_number": "1",
            "city": "Test City",
            "zipcode": "12345",
            "country_id": country.alpha_2,
            "phone": "+1234567890",
        }

        # Execute: Create order from cart (this simulates payment confirmation)
        # This should convert reservations to stock decrements
        order = OrderService.create_order_from_cart(
            cart=cart,
            shipping_address=shipping_address,
            payment_intent_id=payment_intent_id,
            pay_way=pay_way,
            user=user,
        )

        # Verify: Order was created successfully
        assert order is not None, f"Order should be created for {description}"
        assert order.payment_id == payment_intent_id
        assert order.status == OrderStatus.PENDING

        # Verify: All reservations are now marked as consumed
        for i, reservation in enumerate(reservations):
            reservation.refresh_from_db()
            assert reservation.consumed is True, (
                f"Reservation {i} should be marked as consumed for {description}"
            )
            assert reservation.order_id == order.id, (
                f"Reservation {i} should be linked to order {order.id} for {description}"
            )

        # Verify: Stock has been decremented correctly EXACTLY ONCE
        # Stock should be decremented by StockManager.convert_reservation_to_sale
        # and NOT by any signal handlers (to avoid double-decrementing)
        for i, product in enumerate(products):
            product.refresh_from_db()
            expected_stock = initial_stocks[i] - quantities[i]
            actual_stock = product.stock

            # Stock must be decremented EXACTLY once - no more, no less
            assert actual_stock == expected_stock, (
                f"Product {i} stock should be EXACTLY {expected_stock} "
                f"(initial {initial_stocks[i]} - quantity {quantities[i]}) "
                f"for {description}, but got {actual_stock}. "
                f"Stock should be decremented EXACTLY ONCE."
            )

        # Verify: StockLog entries were created for each conversion
        # There should be EXACTLY ONE DECREMENT log per product (from convert_reservation_to_sale)
        for i, product in enumerate(products):
            decrement_logs = StockLog.objects.filter(
                product=product,
                order=order,
                operation_type=StockLog.OPERATION_DECREMENT,
            )
            assert decrement_logs.count() == 1, (
                f"Should have EXACTLY 1 DECREMENT log for product {i} for {description}, "
                f"but found {decrement_logs.count()}. Stock should only be decremented once."
            )

            # Verify the log has correct quantity delta
            log = decrement_logs.first()
            assert log.quantity_delta == -quantities[i], (
                f"Log should record quantity -{quantities[i]} for product {i} for {description}"
            )
            # Verify stock_after is consistent with stock_before and quantity_delta
            assert log.stock_after == log.stock_before + log.quantity_delta, (
                f"Log should show consistent before/after for product {i}: "
                f"{log.stock_before} + {log.quantity_delta} = {log.stock_after}"
            )

        # Verify: Order metadata contains reservation IDs
        assert "stock_reservation_ids" in order.metadata, (
            f"Order metadata should contain stock_reservation_ids for {description}"
        )
        reservation_ids_in_metadata = order.metadata["stock_reservation_ids"]
        assert len(reservation_ids_in_metadata) == num_products, (
            f"Order metadata should contain {num_products} reservation IDs for {description}"
        )
        for reservation in reservations:
            assert reservation.id in reservation_ids_in_metadata, (
                f"Reservation {reservation.id} should be in order metadata for {description}"
            )

    def test_reservation_conversion_is_atomic(
        self, mock_payment_validation, country
    ):
        """
        Test that reservation conversion is atomic - all or nothing.

        This test verifies that if one reservation conversion fails, the entire
        order creation is rolled back and no reservations are consumed.
        """
        # Setup: Create two products with sufficient stock
        product1 = ProductFactory(stock=50)
        product2 = ProductFactory(stock=50)

        # Create cart with items
        user = UserAccountFactory()
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product1, quantity=10)
        CartItemFactory(cart=cart, product=product2, quantity=10)

        # Create reservations
        reservation1 = StockManager.reserve_stock(
            product_id=product1.id,
            quantity=10,
            session_id=str(cart.uuid),
            user_id=user.id,
        )
        reservation2 = StockManager.reserve_stock(
            product_id=product2.id,
            quantity=10,
            session_id=str(cart.uuid),
            user_id=user.id,
        )

        # Mock the convert_reservation_to_sale to fail on the second reservation
        # This simulates a failure during the conversion process
        original_convert = StockManager.convert_reservation_to_sale
        call_count = [0]

        def mock_convert_with_failure(reservation_id, order_id):
            call_count[0] += 1
            if call_count[0] == 2:
                # Fail on second conversion with proper exception arguments
                from order.exceptions import InsufficientStockError

                raise InsufficientStockError(
                    product_id=product2.id, available=0, requested=10
                )
            return original_convert(reservation_id, order_id)

        # Attempt to create order - should fail during conversion
        pay_way = PayWayFactory(provider_code="stripe")
        shipping_address = {
            "first_name": "Test",
            "last_name": "User",
            "email": "test@example.com",
            "street": "123 Test St",
            "street_number": "1",
            "city": "Test City",
            "zipcode": "12345",
            "country_id": country.alpha_2,
            "phone": "+1234567890",
        }

        from order.exceptions import InsufficientStockError

        with patch.object(
            StockManager,
            "convert_reservation_to_sale",
            side_effect=mock_convert_with_failure,
        ):
            with pytest.raises(InsufficientStockError):
                OrderService.create_order_from_cart(
                    cart=cart,
                    shipping_address=shipping_address,
                    payment_intent_id="pi_test_atomic",
                    pay_way=pay_way,
                    user=user,
                )

        # Verify: Neither reservation was consumed (atomicity)
        reservation1.refresh_from_db()
        reservation2.refresh_from_db()
        assert reservation1.consumed is False, (
            "Reservation 1 should not be consumed after failed order creation"
        )
        assert reservation2.consumed is False, (
            "Reservation 2 should not be consumed after failed order creation"
        )

        # Verify: No order was created
        orders = Order.objects.filter(payment_id="pi_test_atomic")
        assert orders.count() == 0, (
            "No order should be created after failed conversion"
        )

        # Verify: Product stocks unchanged (rollback)
        product1.refresh_from_db()
        product2.refresh_from_db()
        assert product1.stock == 50, (
            "Product 1 stock should be unchanged after rollback"
        )
        assert product2.stock == 50, (
            "Product 2 stock should be unchanged after rollback"
        )

    def test_reservation_conversion_creates_correct_audit_logs(
        self, mock_payment_validation, country
    ):
        """
        Test that reservation conversion creates correct StockLog audit entries.
        """
        # Setup: Create product
        product = ProductFactory(stock=100)

        # Create cart and reservation
        user = UserAccountFactory()
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=25)

        StockManager.reserve_stock(
            product_id=product.id,
            quantity=25,
            session_id=str(cart.uuid),
            user_id=user.id,
        )

        # Create order
        pay_way = PayWayFactory(provider_code="stripe")
        shipping_address = {
            "first_name": "Test",
            "last_name": "User",
            "email": "test@example.com",
            "street": "123 Test St",
            "street_number": "1",
            "city": "Test City",
            "zipcode": "12345",
            "country_id": country.alpha_2,
            "phone": "+1234567890",
        }

        order = OrderService.create_order_from_cart(
            cart=cart,
            shipping_address=shipping_address,
            payment_intent_id="pi_test_audit_logs",
            pay_way=pay_way,
            user=user,
        )

        # Verify: RESERVE log was created during reservation
        reserve_logs = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_RESERVE
        )
        assert reserve_logs.count() == 1, "Should have 1 RESERVE log entry"

        # Verify: DECREMENT log was created during conversion
        # There should be EXACTLY ONE decrement log (from convert_reservation_to_sale)
        decrement_logs = StockLog.objects.filter(
            product=product,
            order=order,
            operation_type=StockLog.OPERATION_DECREMENT,
        )
        assert decrement_logs.count() == 1, (
            f"Should have EXACTLY 1 DECREMENT log entry, found {decrement_logs.count()}. "
            f"Stock should only be decremented once."
        )

        # Get the decrement log (should be only one)
        decrement_log = decrement_logs.first()
        assert decrement_log.quantity_delta == -25, (
            f"Log should record quantity -25, got {decrement_log.quantity_delta}"
        )
        # Stock before should be 100 (initial stock)
        assert decrement_log.stock_before == 100, (
            f"Log should show stock_before as 100, got {decrement_log.stock_before}"
        )
        assert decrement_log.stock_after == 75, (
            f"Log should show stock_after as 75 (100 - 25), got {decrement_log.stock_after}"
        )
        assert str(order.id) in decrement_log.reason, (
            "DECREMENT log reason should mention order ID"
        )
        assert decrement_log.performed_by == user, (
            "DECREMENT log should record the user who performed the action"
        )
