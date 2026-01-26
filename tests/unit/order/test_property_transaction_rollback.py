from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.utils import timezone
from djmoney.money import Money

from cart.models import Cart, CartItem
from order.enum.status import OrderStatus, PaymentStatus
from order.models import Order, OrderItem
from order.models.stock_log import StockLog
from order.services import OrderService
from order.stock import StockManager
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory
from country.factories import CountryFactory


@pytest.mark.django_db(transaction=True)
class TestProperty20TransactionFailuresRollbackCompletely:
    """
    Test that database transactions rollback completely when exceptions occur,
    leaving no partial changes in the database.
    """

    def setup_method(self):
        """Set up test data for each test method."""
        self.user = UserAccountFactory.create()
        self.pay_way = PayWayFactory.create()
        self.country = CountryFactory.create()

        self.shipping_address = {
            "first_name": "Test",
            "last_name": "User",
            "email": "test@example.com",
            "street": "Test St",
            "street_number": "123",
            "city": "Test City",
            "zipcode": "12345",
            "country_id": self.country.alpha_2,  # Country uses alpha_2 as primary key
            "phone": "+1234567890",
        }

    @pytest.mark.parametrize(
        "failure_point,initial_stock,order_quantity,expected_exception",
        [
            # Failure during order creation after stock decrement
            ("order_save", 100, 10, Exception),
            ("order_save", 50, 25, Exception),
            ("order_save", 20, 5, Exception),
            # Failure during order item creation after stock decrement
            ("order_item_save", 100, 10, Exception),
            ("order_item_save", 50, 25, Exception),
            # Failure during stock log creation
            ("stock_log_save", 100, 10, Exception),
            ("stock_log_save", 50, 25, Exception),
        ],
        ids=[
            "order_save_fails_stock_100_qty_10",
            "order_save_fails_stock_50_qty_25",
            "order_save_fails_stock_20_qty_5",
            "order_item_save_fails_stock_100_qty_10",
            "order_item_save_fails_stock_50_qty_25",
            "stock_log_save_fails_stock_100_qty_10",
            "stock_log_save_fails_stock_50_qty_25",
        ],
    )
    @patch("order.payment.get_payment_provider")
    def test_order_creation_rollback_on_failure(
        self,
        mock_get_provider,
        failure_point,
        initial_stock,
        order_quantity,
        expected_exception,
    ):
        """
        Test that order creation rolls back completely when any part fails.

        Simulates failures at different points during order creation and verifies:
        1. Product stock is unchanged (rolled back)
        2. No Order record is created
        3. No OrderItem records are created
        4. No StockLog records are persisted
        5. No StockReservation records are consumed
        """
        # Mock payment provider
        mock_provider = Mock()
        mock_provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"status": "succeeded", "amount": order_quantity * 5000},
        )
        mock_get_provider.return_value = mock_provider

        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=initial_stock
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Record initial state
        initial_product_stock = product.stock
        initial_order_count = Order.objects.count()
        initial_order_item_count = OrderItem.objects.count()
        initial_stock_log_count = StockLog.objects.filter(
            product=product
        ).count()

        # Create cart with item
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(
            cart=cart, product=product, quantity=order_quantity
        )

        payment_intent_id = (
            f"pi_test_rollback_{failure_point}_{timezone.now().timestamp()}"
        )

        # Simulate failure at specified point
        if failure_point == "order_save":
            # Patch Order.save to raise exception
            with patch(
                "order.models.order.Order.save",
                side_effect=Exception("Order save failed"),
            ):
                with pytest.raises(
                    expected_exception, match="Order save failed"
                ):
                    OrderService.create_order_from_cart(
                        cart=cart,
                        shipping_address=self.shipping_address,
                        payment_intent_id=payment_intent_id,
                        pay_way=self.pay_way,
                        user=self.user,
                    )

        elif failure_point == "order_item_save":
            # Patch OrderItem.save to raise exception
            with patch(
                "order.models.OrderItem.save",
                side_effect=Exception("OrderItem save failed"),
            ):
                with pytest.raises(
                    expected_exception, match="OrderItem save failed"
                ):
                    OrderService.create_order_from_cart(
                        cart=cart,
                        shipping_address=self.shipping_address,
                        payment_intent_id=payment_intent_id,
                        pay_way=self.pay_way,
                        user=self.user,
                    )

        elif failure_point == "stock_log_save":
            # Patch StockLog.save to raise exception
            with patch(
                "order.models.stock_log.StockLog.save",
                side_effect=Exception("StockLog save failed"),
            ):
                with pytest.raises(
                    expected_exception, match="StockLog save failed"
                ):
                    OrderService.create_order_from_cart(
                        cart=cart,
                        shipping_address=self.shipping_address,
                        payment_intent_id=payment_intent_id,
                        pay_way=self.pay_way,
                        user=self.user,
                    )

        # Verify complete rollback - no partial changes persisted

        # 1. Product stock unchanged
        product.refresh_from_db()
        assert product.stock == initial_product_stock, (
            f"Product stock should be rolled back to {initial_product_stock}, "
            f"but got {product.stock}. Partial stock decrement persisted!"
        )

        # 2. No Order created
        final_order_count = Order.objects.count()
        assert final_order_count == initial_order_count, (
            f"No Order should be created after rollback. "
            f"Expected {initial_order_count} orders, but found {final_order_count}"
        )

        # Verify no order with this payment_id exists
        assert not Order.objects.filter(
            payment_id=payment_intent_id
        ).exists(), (
            f"Order with payment_id {payment_intent_id} should not exist after rollback"
        )

        # 3. No OrderItem created
        final_order_item_count = OrderItem.objects.count()
        assert final_order_item_count == initial_order_item_count, (
            f"No OrderItem should be created after rollback. "
            f"Expected {initial_order_item_count} items, but found {final_order_item_count}"
        )

        # 4. No StockLog persisted
        final_stock_log_count = StockLog.objects.filter(product=product).count()
        assert final_stock_log_count == initial_stock_log_count, (
            f"No StockLog should be persisted after rollback. "
            f"Expected {initial_stock_log_count} logs, but found {final_stock_log_count}"
        )

    @pytest.mark.parametrize(
        "failure_point,initial_stock,order_quantity",
        [
            # Failure during order status update - this WILL raise and rollback
            ("order_status_update", 50, 10),
            ("order_status_update", 30, 15),
        ],
        ids=[
            "cancel_order_status_fails_stock_50_qty_10",
            "cancel_order_status_fails_stock_30_qty_15",
        ],
    )
    def test_order_cancellation_rollback_on_failure(
        self, failure_point, initial_stock, order_quantity
    ):
        """
        Test that order cancellation rolls back completely when critical operations fail.

        Note: The service is designed to be resilient - it logs errors for stock operations
        but continues with cancellation. Only critical failures (like order.save) cause
        full rollback. This test focuses on those critical failure points.

        Simulates failures during order cancellation and verifies:
        1. Product stock is unchanged (not incremented)
        2. Order status is unchanged
        3. No StockLog records are persisted
        """
        # Create product with initial stock (already decremented)
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY),
            stock=initial_stock,
            vat=None,
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create a completed order
        order = Order.objects.create(
            user=self.user,
            pay_way=self.pay_way,
            country=self.country,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
            payment_id=f"pi_test_{timezone.now().timestamp()}",
            **self.shipping_address,
        )

        # Create order item
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=order_quantity,
            price=product.price,
        )

        # Record initial state
        initial_product_stock = product.stock
        initial_order_status = order.status
        initial_stock_log_count = StockLog.objects.filter(
            product=product
        ).count()

        # Simulate failure at specified point
        if failure_point == "order_status_update":
            # Patch Order.save to raise exception when status changes
            original_save = Order.save

            def failing_save(self, *args, **kwargs):
                if hasattr(self, "_state") and self._state.adding is False:
                    # This is an update, not a create
                    raise Exception("Order status update failed")
                return original_save(self, *args, **kwargs)

            with patch.object(Order, "save", failing_save):
                with pytest.raises(
                    Exception, match="Order status update failed"
                ):
                    OrderService.cancel_order(order, reason="Test cancellation")

        # Verify complete rollback - no partial changes persisted

        # 1. Product stock unchanged (not incremented)
        product.refresh_from_db()
        assert product.stock == initial_product_stock, (
            f"Product stock should remain {initial_product_stock} after rollback, "
            f"but got {product.stock}. Partial stock increment persisted!"
        )

        # 2. Order status unchanged
        order.refresh_from_db()
        assert order.status == initial_order_status, (
            f"Order status should remain {initial_order_status} after rollback, "
            f"but got {order.status}. Partial status change persisted!"
        )

        # 3. No new StockLog persisted
        final_stock_log_count = StockLog.objects.filter(product=product).count()
        assert final_stock_log_count == initial_stock_log_count, (
            f"No new StockLog should be persisted after rollback. "
            f"Expected {initial_stock_log_count} logs, but found {final_stock_log_count}"
        )

    def test_order_cancellation_resilient_to_stock_failures(self):
        """
        Test that order cancellation continues even if stock operations fail.

        **Validates: Service Resilience**

        The service is designed to be resilient - if stock increment fails during
        cancellation, it logs the error but still cancels the order. This ensures
        orders can be canceled even if there are stock management issues.

        This is intentional behavior for production resilience.
        """
        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=50, vat=None
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create a completed order
        order = Order.objects.create(
            user=self.user,
            pay_way=self.pay_way,
            country=self.country,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
            payment_id=f"pi_test_{timezone.now().timestamp()}",
            **self.shipping_address,
        )

        # Create order item
        OrderItem.objects.create(
            order=order, product=product, quantity=10, price=product.price
        )

        # Patch StockManager.increment_stock to raise exception
        with patch(
            "order.stock.StockManager.increment_stock",
            side_effect=Exception("Stock increment failed"),
        ):
            # Service should NOT raise - it logs the error and continues
            canceled_order, refund_info = OrderService.cancel_order(
                order, reason="Test cancellation", refund_payment=False
            )

        # Verify order was still canceled despite stock failure
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED, (
            f"Order should be canceled despite stock failure, "
            f"but status is {order.status}"
        )

        # Verify cancellation metadata was added
        assert "cancellation" in order.metadata, (
            "Cancellation metadata should be present"
        )
        assert order.metadata["cancellation"]["reason"] == "Test cancellation"

    @pytest.mark.parametrize(
        "failure_point,initial_stock,reservation_quantity",
        [
            # Failure during payment success handling
            ("order_status_update_payment", 100, 10),
            ("order_status_update_payment", 50, 25),
        ],
        ids=[
            "payment_success_status_fails_stock_100_qty_10",
            "payment_success_status_fails_stock_50_qty_25",
        ],
    )
    def test_payment_success_rollback_on_failure(
        self, failure_point, initial_stock, reservation_quantity
    ):
        """
        Test that payment success handling rolls back completely when critical operations fail.

        Note: handle_payment_succeeded doesn't have explicit error handling for
        reservation conversion failures - if conversion fails, the exception propagates
        and the transaction rolls back. This test verifies that behavior.

        Simulates failures during payment success handling and verifies:
        1. Product stock is unchanged
        2. Order status is unchanged
        3. Reservations are not consumed
        4. No StockLog records are persisted
        """
        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY),
            stock=initial_stock,
            vat=None,
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create stock reservation
        session_id = f"test_session_{timezone.now().timestamp()}"
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=reservation_quantity,
            session_id=session_id,
            user_id=self.user.id,
        )

        # Create order in PENDING status
        payment_intent_id = f"pi_test_{timezone.now().timestamp()}"
        order = Order.objects.create(
            user=self.user,
            pay_way=self.pay_way,
            country=self.country,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_intent_id,
            metadata={"stock_reservation_ids": [reservation.id]},
            **self.shipping_address,
        )

        # Create order item
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=reservation_quantity,
            price=product.price,
        )

        # Record initial state
        initial_product_stock = product.stock
        initial_order_status = order.status
        initial_reservation_consumed = reservation.consumed
        initial_stock_log_count = StockLog.objects.filter(
            product=product
        ).count()

        # Simulate failure at specified point
        if failure_point == "order_status_update_payment":
            # Patch Order.save to raise exception when status changes
            original_save = Order.save

            def failing_save(self, *args, **kwargs):
                if hasattr(self, "_state") and self._state.adding is False:
                    # This is an update, not a create
                    if self.status == OrderStatus.PROCESSING:
                        raise Exception("Order status update failed")
                return original_save(self, *args, **kwargs)

            with patch.object(Order, "save", failing_save):
                with pytest.raises(
                    Exception, match="Order status update failed"
                ):
                    OrderService.handle_payment_succeeded(payment_intent_id)

        # Verify complete rollback - no partial changes persisted

        # 1. Product stock unchanged
        product.refresh_from_db()
        assert product.stock == initial_product_stock, (
            f"Product stock should remain {initial_product_stock} after rollback, "
            f"but got {product.stock}. Partial stock decrement persisted!"
        )

        # 2. Order status unchanged
        order.refresh_from_db()
        assert order.status == initial_order_status, (
            f"Order status should remain {initial_order_status} after rollback, "
            f"but got {order.status}. Partial status change persisted!"
        )

        # 3. Reservation not consumed
        reservation.refresh_from_db()
        assert reservation.consumed == initial_reservation_consumed, (
            f"Reservation consumed flag should remain {initial_reservation_consumed} after rollback, "
            f"but got {reservation.consumed}. Partial reservation consumption persisted!"
        )

        # 4. No new StockLog persisted (beyond the initial reservation log)
        final_stock_log_count = StockLog.objects.filter(product=product).count()
        assert final_stock_log_count == initial_stock_log_count, (
            f"No new StockLog should be persisted after rollback. "
            f"Expected {initial_stock_log_count} logs, but found {final_stock_log_count}"
        )

    def test_payment_failure_handling_is_resilient(self):
        """
        Test that payment failure handling is resilient to errors.

        **Validates: Service Resilience**

        The handle_payment_failed method is simple - it just marks the order as failed.
        It doesn't attempt to release reservations or perform complex operations.
        This test verifies that the method works correctly.
        """
        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100, vat=None
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create stock reservation
        session_id = f"test_session_{timezone.now().timestamp()}"
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id=session_id,
            user_id=self.user.id,
        )

        # Create order in PENDING status
        payment_intent_id = f"pi_test_{timezone.now().timestamp()}"
        order = Order.objects.create(
            user=self.user,
            pay_way=self.pay_way,
            country=self.country,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_intent_id,
            metadata={"stock_reservation_ids": [reservation.id]},
            **self.shipping_address,
        )

        # Call handle_payment_failed
        result = OrderService.handle_payment_failed(payment_intent_id)

        # Verify order was marked as failed
        assert result is not None, (
            "handle_payment_failed should return the order"
        )
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED, (
            f"Order payment status should be FAILED, but got {order.payment_status}"
        )

        # Note: Reservation cleanup happens via periodic task, not in handle_payment_failed

    def test_multiple_operations_rollback_atomically(self):
        """
        Test that complex transactions with multiple operations rollback atomically.

        Tests a scenario where multiple database operations occur in a transaction
        and verifies that ALL operations are rolled back when any one fails.
        """
        # Create multiple products
        products = []
        for i in range(3):
            product = ProductFactory.create(
                price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100
            )
            product.set_current_language("en")
            product.name = f"Test Product {i + 1}"
            product.save()
            products.append(product)

        # Record initial state for all products
        initial_stocks = {p.id: p.stock for p in products}
        initial_order_count = Order.objects.count()
        initial_order_item_count = OrderItem.objects.count()
        initial_stock_log_count = StockLog.objects.count()

        # Create cart with multiple items
        cart = Cart.objects.create(user=self.user)
        for product in products:
            CartItem.objects.create(cart=cart, product=product, quantity=10)

        payment_intent_id = f"pi_test_multi_{timezone.now().timestamp()}"

        # Mock payment provider
        with patch("order.payment.get_payment_provider") as mock_get_provider:
            mock_provider = Mock()
            mock_provider.get_payment_status.return_value = (
                PaymentStatus.COMPLETED,
                {"status": "succeeded", "amount": 150000},
            )
            mock_get_provider.return_value = mock_provider

            # Simulate failure after processing some items
            # Patch OrderItem.save to fail on the 3rd item
            call_count = {"count": 0}
            original_save = OrderItem.save

            def failing_save(self, *args, **kwargs):
                call_count["count"] += 1
                if call_count["count"] >= 3:
                    raise Exception("OrderItem save failed on 3rd item")
                return original_save(self, *args, **kwargs)

            with patch.object(OrderItem, "save", failing_save):
                with pytest.raises(
                    Exception, match="OrderItem save failed on 3rd item"
                ):
                    OrderService.create_order_from_cart(
                        cart=cart,
                        shipping_address=self.shipping_address,
                        payment_intent_id=payment_intent_id,
                        pay_way=self.pay_way,
                        user=self.user,
                    )

        # Verify complete rollback for ALL products
        for product in products:
            product.refresh_from_db()
            assert product.stock == initial_stocks[product.id], (
                f"Product {product.id} stock should be rolled back to {initial_stocks[product.id]}, "
                f"but got {product.stock}. Partial changes persisted!"
            )

        # Verify no orders or items created
        assert Order.objects.count() == initial_order_count, (
            "No Order should be created after rollback"
        )
        assert OrderItem.objects.count() == initial_order_item_count, (
            "No OrderItem should be created after rollback"
        )

        # Verify no stock logs persisted
        assert StockLog.objects.count() == initial_stock_log_count, (
            "No StockLog should be persisted after rollback"
        )
