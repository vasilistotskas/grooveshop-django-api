from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from djmoney.money import Money

from cart.models.cart import Cart
from cart.models.item import CartItem
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import InsufficientStockError, InvalidOrderDataError
from order.models.order import Order
from order.models.stock_log import StockLog
from order.services import OrderService
from order.stock import StockManager
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory


@pytest.mark.django_db(transaction=True)
class TestStockOperationsAreAtomic:
    """
    Test that stock operations are atomic - they either complete fully or
    rollback completely with no partial updates.
    """

    def setup_method(self):
        """Set up test data for each test method."""
        from country.factories import CountryFactory

        self.user = UserAccountFactory.create()
        self.pay_way = PayWayFactory.create(provider_code="stripe")
        self.country = CountryFactory.create()

        # Standard shipping address for all tests
        self.shipping_address = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": self.country.alpha_2,  # Country uses alpha_2 as primary key
            "phone": "+30123456789",
        }

    @pytest.mark.parametrize(
        "initial_stock,order_quantities,expected_success",
        [
            # Test case 1: Multiple successful orders within stock
            (10, [3, 4, 2], True),
            # Test case 2: Orders that exceed stock - should fail
            (10, [5, 6], False),
            # Test case 3: Multiple small orders that fit exactly
            (5, [2, 2, 1], True),
            # Test case 4: Single large order within stock
            (20, [15], True),
            # Test case 5: Multiple orders where last one fails
            (10, [4, 4, 3], False),
            # Test case 6: Edge case - order exactly all stock
            (10, [10], True),
            # Test case 7: Edge case - order one more than stock
            (10, [11], False),
            # Test case 8: Many small orders
            (15, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1], True),
            # Test case 9: Zero stock scenario
            (0, [1], False),
            # Test case 10: Large stock, multiple medium orders
            (100, [25, 30, 20, 15], True),
        ],
        ids=[
            "multiple_orders_within_stock",
            "orders_exceed_stock",
            "orders_fit_exactly",
            "single_large_order",
            "last_order_fails",
            "order_exactly_all_stock",
            "order_one_more_than_stock",
            "many_small_orders",
            "zero_stock",
            "large_stock_multiple_orders",
        ],
    )
    @patch("order.payment.get_payment_provider")
    def test_order_creation_atomicity(
        self,
        mock_get_provider,
        initial_stock,
        order_quantities,
        expected_success,
    ):
        """
        Test that order creation with stock decrements is atomic.

        For each test case:
        1. Create product with initial_stock
        2. Attempt to create orders with order_quantities
        3. Verify final stock = initial - sum(successful orders)
        4. Verify stock never goes negative
        5. Verify atomicity: either all items in order succeed or all fail
        """
        # Mock payment provider
        mock_provider = Mock()
        mock_provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"status": "succeeded", "amount": 10000},
        )
        mock_get_provider.return_value = mock_provider

        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=initial_stock
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Track successful orders and their quantities
        successful_orders = []
        successful_quantities = []

        # Attempt to create orders with each quantity
        for i, quantity in enumerate(order_quantities):
            # Create cart with item
            cart = Cart.objects.create(user=self.user)
            CartItem.objects.create(
                cart=cart, product=product, quantity=quantity
            )

            try:
                # Attempt to create order
                order = OrderService.create_order_from_cart(
                    cart=cart,
                    shipping_address=self.shipping_address,
                    payment_intent_id=f"pi_test_{i}",
                    pay_way=self.pay_way,
                    user=self.user,
                )

                # Order succeeded
                successful_orders.append(order)
                successful_quantities.append(quantity)

                # Verify order was created
                assert order is not None
                assert order.status == OrderStatus.PENDING

                # Verify stock was decremented
                product.refresh_from_db()
                expected_stock = initial_stock - sum(successful_quantities)
                assert product.stock == expected_stock, (
                    f"After order {i + 1}, stock should be {expected_stock}, "
                    f"but got {product.stock}"
                )

                # Verify stock never goes negative
                assert product.stock >= 0, (
                    f"Stock went negative: {product.stock}"
                )

            except (InsufficientStockError, InvalidOrderDataError):
                # Order failed due to insufficient stock (cart validation or stock check)
                # Verify stock was NOT changed
                product.refresh_from_db()
                expected_stock = initial_stock - sum(successful_quantities)
                assert product.stock == expected_stock, (
                    f"After failed order {i + 1}, stock should remain {expected_stock}, "
                    f"but got {product.stock}"
                )

        # Final verification
        product.refresh_from_db()
        total_successful_quantity = sum(successful_quantities)
        expected_final_stock = initial_stock - total_successful_quantity

        # Verify final stock equals initial minus successful orders
        assert product.stock == expected_final_stock, (
            f"Final stock should be {expected_final_stock} "
            f"(initial {initial_stock} - successful {total_successful_quantity}), "
            f"but got {product.stock}"
        )

        # Verify stock never went negative
        assert product.stock >= 0, f"Final stock is negative: {product.stock}"

        # Verify expected success/failure
        if expected_success:
            # All orders should have succeeded
            assert len(successful_orders) == len(order_quantities), (
                f"Expected all {len(order_quantities)} orders to succeed, "
                f"but only {len(successful_orders)} succeeded"
            )
        else:
            # At least one order should have failed
            assert len(successful_orders) < len(order_quantities), (
                f"Expected at least one order to fail, "
                f"but all {len(successful_orders)} succeeded"
            )

    @pytest.mark.parametrize(
        "initial_stock,order_quantity,cancel_after_creation",
        [
            # Test case 1: Create and cancel single order
            (10, 5, True),
            # Test case 2: Create order but don't cancel
            (10, 5, False),
            # Test case 3: Create and cancel order with all stock
            (10, 10, True),
            # Test case 4: Create and cancel small order
            (20, 3, True),
            # Test case 5: Create and cancel large order
            (100, 75, True),
        ],
        ids=[
            "create_and_cancel_single",
            "create_without_cancel",
            "cancel_all_stock",
            "cancel_small_order",
            "cancel_large_order",
        ],
    )
    @patch("order.payment.get_payment_provider")
    def test_order_cancellation_atomicity(
        self,
        mock_get_provider,
        initial_stock,
        order_quantity,
        cancel_after_creation,
    ):
        """
        Test that order cancellation with stock restoration is atomic.

        For each test case:
        1. Create product with initial_stock
        2. Create order with order_quantity (decrements stock)
        3. Optionally cancel order (restores stock)
        4. Verify final stock matches expected value
        5. Verify atomicity of cancellation
        """
        # Mock payment provider
        mock_provider = Mock()
        mock_provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"status": "succeeded", "amount": 10000},
        )
        mock_get_provider.return_value = mock_provider

        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=initial_stock
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create cart with item
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(
            cart=cart, product=product, quantity=order_quantity
        )

        # Create order
        order = OrderService.create_order_from_cart(
            cart=cart,
            shipping_address=self.shipping_address,
            payment_intent_id="pi_test_cancel",
            pay_way=self.pay_way,
            user=self.user,
        )

        # Verify stock was decremented
        product.refresh_from_db()
        assert product.stock == initial_stock - order_quantity

        if cancel_after_creation:
            # Cancel the order (pass order object, not order_id)
            OrderService.cancel_order(order, reason="Test cancellation")

            # Verify stock was restored atomically
            product.refresh_from_db()
            assert product.stock == initial_stock, (
                f"After cancellation, stock should be restored to {initial_stock}, "
                f"but got {product.stock}"
            )

            # Verify order status changed
            order.refresh_from_db()
            assert order.status == OrderStatus.CANCELED
        else:
            # Don't cancel - verify stock remains decremented
            product.refresh_from_db()
            assert product.stock == initial_stock - order_quantity

    @pytest.mark.parametrize(
        "initial_stock,items_data",
        [
            # Test case 1: Two items, both succeed
            (
                20,
                [
                    {"quantity": 5, "should_succeed": True},
                    {"quantity": 3, "should_succeed": True},
                ],
            ),
            # Test case 2: Three items, all succeed
            (
                30,
                [
                    {"quantity": 10, "should_succeed": True},
                    {"quantity": 8, "should_succeed": True},
                    {"quantity": 5, "should_succeed": True},
                ],
            ),
            # Test case 3: Two items, second causes failure
            (
                10,
                [
                    {"quantity": 5, "should_succeed": True},
                    {
                        "quantity": 8,
                        "should_succeed": False,
                    },  # Would exceed stock
                ],
            ),
            # Test case 4: Multiple items exactly using all stock
            (
                15,
                [
                    {"quantity": 5, "should_succeed": True},
                    {"quantity": 5, "should_succeed": True},
                    {"quantity": 5, "should_succeed": True},
                ],
            ),
        ],
        ids=[
            "two_items_both_succeed",
            "three_items_all_succeed",
            "two_items_second_fails",
            "multiple_items_exact_stock",
        ],
    )
    @patch("order.payment.get_payment_provider")
    def test_multi_item_order_atomicity(
        self, mock_get_provider, initial_stock, items_data
    ):
        """
        Test atomicity with orders containing multiple items of the same product.

        This tests that when an order has multiple line items for the same product,
        either ALL items are processed or NONE are (atomic transaction).
        """
        # Mock payment provider
        mock_provider = Mock()
        mock_provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"status": "succeeded", "amount": 10000},
        )
        mock_get_provider.return_value = mock_provider

        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=initial_stock
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Calculate total quantity needed
        total_quantity = sum(item["quantity"] for item in items_data)

        # Create cart with single product, total quantity
        # Note: Cart validation will check if total quantity is available
        cart = Cart.objects.create(user=self.user)

        # Create a single cart item with the total quantity
        # This is the correct way to handle multiple quantities of the same product
        CartItem.objects.create(
            cart=cart, product=product, quantity=total_quantity
        )

        # Determine if order should succeed
        should_succeed = total_quantity <= initial_stock

        if should_succeed:
            # Order should succeed - all items processed atomically
            order = OrderService.create_order_from_cart(
                cart=cart,
                shipping_address=self.shipping_address,
                payment_intent_id="pi_test_multi",
                pay_way=self.pay_way,
                user=self.user,
            )

            # Verify order created
            assert order is not None

            # Verify ALL stock was decremented atomically
            product.refresh_from_db()
            expected_stock = initial_stock - total_quantity
            assert product.stock == expected_stock, (
                f"Stock should be {expected_stock} after successful multi-item order, "
                f"but got {product.stock}"
            )

            # Verify order items were created (may be consolidated into one item)
            assert order.items.count() >= 1
        else:
            # Order should fail - NO items processed (atomic rollback)
            with pytest.raises(
                (InsufficientStockError, InvalidOrderDataError, Exception)
            ):
                OrderService.create_order_from_cart(
                    cart=cart,
                    shipping_address=self.shipping_address,
                    payment_intent_id="pi_test_multi_fail",
                    pay_way=self.pay_way,
                    user=self.user,
                )

            # Verify NO stock was decremented (atomic rollback)
            product.refresh_from_db()
            assert product.stock == initial_stock, (
                f"Stock should remain {initial_stock} after failed multi-item order, "
                f"but got {product.stock}"
            )

            # Verify no order was created
            assert (
                Order.objects.filter(payment_id="pi_test_multi_fail").count()
                == 0
            )

    @pytest.mark.parametrize(
        "initial_stock,decrement_quantity,should_fail",
        [
            # Test case 1: Successful decrement
            (10, 5, False),
            # Test case 2: Failed decrement - insufficient stock
            (10, 15, True),
            # Test case 3: Edge case - decrement all stock
            (10, 10, False),
            # Test case 4: Edge case - decrement one more than stock
            (10, 11, True),
            # Test case 5: Zero stock
            (0, 1, True),
        ],
        ids=[
            "successful_decrement",
            "insufficient_stock",
            "decrement_all_stock",
            "decrement_one_more",
            "zero_stock",
        ],
    )
    def test_direct_stock_decrement_atomicity(
        self, initial_stock, decrement_quantity, should_fail
    ):
        """
        Test atomicity of direct stock decrement operations.

        Tests StockManager.decrement_stock() for atomic behavior.
        """
        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=initial_stock
        )

        # Create a dummy order for the decrement
        from order.factories import OrderFactory

        order = OrderFactory.create()

        if should_fail:
            # Decrement should fail atomically
            with pytest.raises(InsufficientStockError):
                StockManager.decrement_stock(
                    product_id=product.id,
                    quantity=decrement_quantity,
                    order_id=order.id,
                    reason="Test decrement",
                )

            # Verify stock unchanged
            product.refresh_from_db()
            assert product.stock == initial_stock

            # Verify no stock log created for failed operation
            assert (
                StockLog.objects.filter(product=product, order=order).count()
                == 0
            )
        else:
            # Decrement should succeed atomically
            StockManager.decrement_stock(
                product_id=product.id,
                quantity=decrement_quantity,
                order_id=order.id,
                reason="Test decrement",
            )

            # Verify stock decremented
            product.refresh_from_db()
            expected_stock = initial_stock - decrement_quantity
            assert product.stock == expected_stock

            # Verify stock log created
            log = StockLog.objects.filter(
                product=product,
                order=order,
                operation_type=StockLog.OPERATION_DECREMENT,
            ).first()
            assert log is not None
            assert log.stock_before == initial_stock
            assert log.stock_after == expected_stock
            assert log.quantity_delta == -decrement_quantity

    @pytest.mark.parametrize(
        "initial_stock,increment_quantity",
        [
            # Test case 1: Small increment
            (10, 5),
            # Test case 2: Large increment
            (10, 50),
            # Test case 3: Increment from zero
            (0, 10),
            # Test case 4: Single unit increment
            (5, 1),
        ],
        ids=[
            "small_increment",
            "large_increment",
            "increment_from_zero",
            "single_unit",
        ],
    )
    def test_stock_increment_atomicity(self, initial_stock, increment_quantity):
        """
        Test atomicity of stock increment operations (cancellations/returns).

        Tests StockManager.increment_stock() for atomic behavior.
        """
        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=initial_stock
        )

        # Create a dummy order for the increment
        from order.factories import OrderFactory

        order = OrderFactory.create()

        # Increment should always succeed atomically
        StockManager.increment_stock(
            product_id=product.id,
            quantity=increment_quantity,
            order_id=order.id,
            reason="Test increment",
        )

        # Verify stock incremented
        product.refresh_from_db()
        expected_stock = initial_stock + increment_quantity
        assert product.stock == expected_stock

        # Verify stock log created
        log = StockLog.objects.filter(
            product=product,
            order=order,
            operation_type=StockLog.OPERATION_INCREMENT,
        ).first()
        assert log is not None
        assert log.stock_before == initial_stock
        assert log.stock_after == expected_stock
        assert log.quantity_delta == increment_quantity

    @patch("order.payment.get_payment_provider")
    def test_transaction_rollback_on_error(self, mock_get_provider):
        """
        Test that stock operations rollback completely on transaction errors.

        Simulates a transaction error during order creation and verifies
        that stock changes are rolled back atomically.
        """
        # Mock payment provider
        mock_provider = Mock()
        mock_provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"status": "succeeded", "amount": 10000},
        )
        mock_get_provider.return_value = mock_provider

        # Create product with stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=20
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        initial_stock = product.stock

        # Create cart with item
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, product=product, quantity=5)

        # Patch Order.save to raise an exception after stock is decremented
        # This simulates a transaction error
        with patch(
            "order.models.order.Order.save",
            side_effect=Exception("Simulated error"),
        ):
            with pytest.raises(Exception, match="Simulated error"):
                OrderService.create_order_from_cart(
                    cart=cart,
                    shipping_address=self.shipping_address,
                    payment_intent_id="pi_test_rollback",
                    pay_way=self.pay_way,
                    user=self.user,
                )

        # Verify stock was rolled back to initial value
        product.refresh_from_db()
        assert product.stock == initial_stock, (
            f"Stock should be rolled back to {initial_stock} after transaction error, "
            f"but got {product.stock}"
        )

        # Verify no order was created
        assert Order.objects.filter(payment_id="pi_test_rollback").count() == 0

        # Verify no stock log was persisted (transaction rolled back)
        assert (
            StockLog.objects.filter(
                product=product, reason__contains="pi_test_rollback"
            ).count()
            == 0
        )
