from decimal import Decimal
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.conf import settings
from djmoney.money import Money

from order.enum.status import OrderStatus
from order.exceptions import (
    InvalidStatusTransitionError,
    OrderCancellationError,
)
from order.models.order import Order
from order.services import OrderService
from order.stock import StockManager
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.assert_english


@pytest.mark.django_db
class OrderServiceTestCase(TestCase):
    def setUp(self):
        self.user = UserAccountFactory.create()

        self.product1 = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=20
        )
        self.product1.set_current_language("en")
        self.product1.name = "Test Product 1"
        self.product1.save()

        self.product2 = ProductFactory.create(
            price=Money("30.00", settings.DEFAULT_CURRENCY), stock=15
        )
        self.product2.set_current_language("en")
        self.product2.name = "Test Product 2"
        self.product2.save()

        self.order_data = {
            "email": "customer@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "phone": "+1234567890",
            "street": "123 Main St",
            "street_number": "Apt 4B",
            "city": "New York",
            "zipcode": "10001",
        }

        self.items_data = [
            {"product": self.product1, "quantity": 2},
            {"product": self.product2, "quantity": 1},
        ]

        self.order = Mock(spec=Order)
        self.order.id = 1
        self.order.uuid = "test-uuid-1234"
        self.order.user = self.user
        self.order.status = OrderStatus.PENDING
        self.order.items = MagicMock()
        self.order.calculate_order_total_amount = Mock(
            return_value=Money("135.00", settings.DEFAULT_CURRENCY)
        )
        self.order.paid_amount = Money("0.00", settings.DEFAULT_CURRENCY)
        self.order.metadata = {}

    def _create_order(self, order_data=None, items_data=None, user=None):
        """Build a real, persisted order with items and decremented stock.

        Stand-in fixture helper for the removed ``OrderService.create_order``
        (the live checkout paths are ``create_order_from_cart`` /
        ``create_order_from_cart_offline``) — kept here purely so the other
        ``OrderService`` methods below (``update_order_status``,
        ``get_user_orders``, ``cancel_order``) still have a real order to
        exercise.
        """
        order_data = order_data if order_data is not None else self.order_data
        items_data = items_data if items_data is not None else self.items_data
        user = user if user is not None else self.user

        order = Order.objects.create(
            user=user, status=OrderStatus.PENDING, **order_data
        )
        for item in items_data:
            product = item["product"]
            quantity = item["quantity"]
            StockManager.decrement_stock(
                product_id=product.id,
                quantity=quantity,
                order_id=order.id,
                reason="test_setup",
            )
            order.items.create(
                product=product,
                price=product.final_price,
                quantity=quantity,
            )
        order.paid_amount = order.calculate_order_total_amount()
        order.save(update_fields=["paid_amount", "paid_amount_currency"])
        return order

    @patch("order.signals.handlers.order_status_changed.send")
    @patch("order.signals.handlers.send_order_confirmation_email")
    def test_update_order_status_valid(self, mock_email, mock_signal):
        order = self._create_order()
        mock_signal.reset_mock()

        OrderService.update_order_status(order, OrderStatus.PROCESSING)

        self.assertEqual(order.status, OrderStatus.PROCESSING)

        mock_signal.assert_called_once()

    def test_update_order_status_invalid(self):
        # A real order is required: update_order_status now re-reads the row
        # under select_for_update (G0285), so a Mock order can't be used.
        order = self._create_order()
        self.assertEqual(order.status, OrderStatus.PENDING)

        with self.assertRaises(InvalidStatusTransitionError) as context:
            OrderService.update_order_status(order, OrderStatus.DELIVERED)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertIn("Cannot transition from", str(context.exception))

    def test_get_user_orders(self):
        order1 = self._create_order()

        order_data_2 = self.order_data.copy()
        order_data_2["email"] = "customer2@example.com"
        order2 = self._create_order(order_data=order_data_2)

        other_user = UserAccountFactory.create()
        order_data_3 = self.order_data.copy()
        order_data_3["email"] = "other@example.com"
        self._create_order(order_data=order_data_3, user=other_user)

        result = OrderService.get_user_orders(self.user.id)

        self.assertEqual(len(result), 2)

        order_ids = [order.id for order in result]
        self.assertIn(order1.id, order_ids)
        self.assertIn(order2.id, order_ids)

    def test_cancel_order(self):
        # Create a real order instead of using a mock
        order = self._create_order()

        # Verify initial state
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertTrue(order.can_be_canceled)

        # Store stock levels AFTER order creation (stock has been decremented)
        self.product1.refresh_from_db()
        self.product2.refresh_from_db()
        initial_stock_1 = self.product1.stock
        initial_stock_2 = self.product2.stock

        canceled_order, refund_info = OrderService.cancel_order(
            order,
            reason="Test cancellation",
            refund_payment=True,
            canceled_by=self.user.id,
        )

        self.assertEqual(canceled_order.status, OrderStatus.CANCELED)
        self.assertIsNone(refund_info)  # No refund since order wasn't paid

        # Verify stock was restored
        self.product1.refresh_from_db()
        self.product2.refresh_from_db()
        self.assertEqual(self.product1.stock, initial_stock_1 + 2)
        self.assertEqual(self.product2.stock, initial_stock_2 + 1)

    @patch("order.signals.order_canceled.send")
    def test_cancel_order_not_cancelable(self, mock_signal):
        # Create a real order in the DB since cancel_order uses select_for_update
        order = self._create_order()
        # Force status to SHIPPED (not cancelable)
        Order.objects.filter(id=order.id).update(status=OrderStatus.SHIPPED)

        order.refresh_from_db()

        with self.assertRaises(OrderCancellationError) as context:
            OrderService.cancel_order(order)

        self.assertIn("cannot be canceled", str(context.exception))

    def test_calculate_shipping_cost(self):
        def mock_calculate_shipping_cost(order_value):
            if order_value.amount > Decimal("100.00"):
                return Money("0.00", settings.DEFAULT_CURRENCY)
            else:
                return Money("10.00", settings.DEFAULT_CURRENCY)

        with patch.object(
            OrderService,
            "calculate_shipping_cost",
            side_effect=mock_calculate_shipping_cost,
        ):
            result = OrderService.calculate_shipping_cost(
                Money("50.00", settings.DEFAULT_CURRENCY)
            )
            self.assertEqual(result, Money("10.00", settings.DEFAULT_CURRENCY))

            result = OrderService.calculate_shipping_cost(
                Money("150.00", settings.DEFAULT_CURRENCY)
            )
            self.assertEqual(result, Money("0.00", settings.DEFAULT_CURRENCY))
