from decimal import Decimal
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import TestCase
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import OrderCancellationError, PaymentError
from order.factories.order import OrderFactory
from order.services import OrderService
from order.stock import StockManager
from product.factories.product import ProductFactory
from tests.utils.shipping import enable_rate
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.assert_english


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), settings.DEFAULT_CURRENCY)


class OrderServiceTestCase(TestCase):
    def test_get_order_by_id(self):
        order = OrderFactory(num_order_items=0)

        self.assertEqual(OrderService.get_order_by_id(order.id).id, order.id)

    def test_get_order_by_uuid(self):
        order = OrderFactory(num_order_items=0)

        result = OrderService.get_order_by_uuid(str(order.uuid))

        self.assertEqual(result.id, order.id)

    def test_get_user_orders_returns_only_that_users_orders(self):
        user = UserAccountFactory(num_addresses=0)
        own = [OrderFactory(user=user, num_order_items=0) for _ in range(2)]
        OrderFactory(
            user=UserAccountFactory(num_addresses=0), num_order_items=0
        )

        result = OrderService.get_user_orders(user.id)

        self.assertEqual({o.id for o in result}, {o.id for o in own})

    @patch("order.signals.order_canceled.send")
    def test_cancel_order_restores_the_stock_it_took(self, _mock_signal):
        order = OrderFactory(status=OrderStatus.PENDING, num_order_items=0)
        product = ProductFactory(stock=10)
        order.items.create(product=product, price=_eur("50.00"), quantity=3)
        # Take the stock the way checkout does. A raw ``product.stock = 7``
        # is indistinguishable from an admin editing the product: it logs
        # against no order (product/signals.py), and cancelling an order
        # must not hand back stock some other actor removed.
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=3,
            order_id=order.id,
            reason="test: order created",
        )

        canceled_order, refund_info = OrderService.cancel_order(
            order=order, reason="Test cancellation", refund_payment=False
        )

        self.assertEqual(canceled_order.status, OrderStatus.CANCELED)
        self.assertIsNone(refund_info)
        product.refresh_from_db()
        self.assertEqual(product.stock, 10)

    @patch("order.signals.handlers.order_canceled.send")
    def test_cancel_paid_order_refunds_it(self, _mock_signal):
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="test_payment_123",
            paid_amount=_eur("100.00"),
            num_order_items=0,
        )
        order.items.create(
            product=ProductFactory(stock=10), price=_eur("50.00"), quantity=2
        )

        with patch.object(OrderService, "refund_order") as mock_refund:
            mock_refund.return_value = (
                True,
                {"refund_id": "refund_123", "status": PaymentStatus.REFUNDED},
            )
            canceled_order, refund_info = OrderService.cancel_order(
                order=order, reason="Customer requested", refund_payment=True
            )

        self.assertEqual(canceled_order.status, OrderStatus.CANCELED)
        self.assertTrue(refund_info["refunded"])
        self.assertEqual(refund_info["refund_id"], "refund_123")
        mock_refund.assert_called_once()

    def test_shipping_cost_is_waived_above_the_free_threshold(self):
        order = OrderFactory(num_order_items=0)
        enable_rate(
            order.country,
            provider_code="flat_rate",
            price=Decimal("5.00"),
            free_shipping_threshold=Decimal("100.00"),
        )

        def quote(order_value):
            return OrderService.shipping_cost(
                order_value=order_value,
                country_id=order.country_id,
                shipping_provider_code="flat_rate",
                shipping_kind="home_delivery",
            )

        self.assertEqual(quote(_eur("49.99")), _eur("5.00"))
        self.assertEqual(quote(_eur("500.00")), _eur("0.00"))

    @patch("order.payment.get_payment_provider")
    def test_refund_order_full(self, mock_get_provider):
        order = OrderFactory(
            payment_status=PaymentStatus.COMPLETED,
            payment_id="test_payment_123",
            paid_amount=_eur("100.00"),
            num_order_items=0,
        )
        mock_get_provider.return_value.refund_payment.return_value = (
            True,
            {"refund_id": "refund_123", "status": PaymentStatus.REFUNDED},
        )

        success, response = OrderService.refund_order(
            order=order, amount=None, reason="Test refund"
        )

        self.assertTrue(success)
        self.assertEqual(response["refund_id"], "refund_123")
        self.assertEqual(order.payment_status, PaymentStatus.REFUNDED)
        self.assertEqual(len(order.metadata["refunds"]), 1)
        self.assertEqual(order.metadata["refunds"][0]["amount"], "full")

    @patch("order.payment.get_payment_provider")
    def test_refund_order_partial(self, mock_get_provider):
        order = OrderFactory(
            payment_status=PaymentStatus.COMPLETED,
            payment_id="test_payment_123",
            paid_amount=_eur("100.00"),
            num_order_items=0,
        )
        mock_get_provider.return_value.refund_payment.return_value = (
            True,
            {
                "refund_id": "refund_456",
                "status": PaymentStatus.PARTIALLY_REFUNDED,
            },
        )

        success, _response = OrderService.refund_order(
            order=order, amount=_eur("25.00"), reason="Partial refund"
        )

        self.assertTrue(success)
        self.assertEqual(order.payment_status, PaymentStatus.PARTIALLY_REFUNDED)
        self.assertEqual(order.metadata["refunds"][0]["amount"], "25.00")

    def test_refund_order_refuses_an_unpaid_order(self):
        order = OrderFactory(
            payment_status=PaymentStatus.PENDING,
            payment_id="test_payment_123",
            num_order_items=0,
        )

        with self.assertRaises(PaymentError) as context:
            OrderService.refund_order(order=order)

        self.assertIn(
            "This order has not been paid yet.", str(context.exception)
        )

    @patch("order.payment.get_payment_provider")
    def test_get_payment_status_persists_the_provider_status(
        self, mock_get_provider
    ):
        order = OrderFactory(
            payment_id="test_payment_123",
            payment_status=PaymentStatus.PENDING,
            num_order_items=0,
        )
        mock_get_provider.return_value.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {
                "payment_id": "test_payment_123",
                "raw_status": "succeeded",
                "provider": "stripe",
            },
        )

        status_enum, status_data = OrderService.get_payment_status(order)

        self.assertEqual(status_enum, PaymentStatus.COMPLETED)
        self.assertEqual(status_data["payment_id"], "test_payment_123")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.COMPLETED)

    def test_add_tracking_info_ships_a_processing_order(self):
        order = OrderFactory(status=OrderStatus.PROCESSING, num_order_items=0)

        updated_order = OrderService.add_tracking_info(
            order=order,
            tracking_number="TRACK123",
            shipping_carrier="DHL",
            auto_update_status=True,
        )

        self.assertEqual(updated_order.tracking_number, "TRACK123")
        self.assertEqual(updated_order.shipping_carrier, "DHL")
        self.assertEqual(updated_order.status, OrderStatus.SHIPPED)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.COMPLETED,
        OrderStatus.CANCELED,
        OrderStatus.RETURNED,
        OrderStatus.REFUNDED,
    ],
)
def test_cancel_order_refuses_an_order_past_processing(status):
    order = OrderFactory(status=status, num_order_items=0)

    with pytest.raises(OrderCancellationError, match="cannot be canceled"):
        OrderService.cancel_order(order=order, refund_payment=False)

    order.refresh_from_db()
    assert order.status == status
