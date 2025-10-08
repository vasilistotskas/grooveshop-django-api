from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService
from product.factories.product import ProductFactory

User = get_user_model()


class OrderServiceTestCase(TestCase):
    def setUp(self):
        self.order = OrderFactory()
        self.user = self.order.user
        self.product = ProductFactory(stock=20)

        test_currency = self.order.shipping_price.currency

        self.order_data = {
            "email": "test@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "phone": "+1234567890",
            "paid_amount": Money(
                amount=Decimal("100.00"), currency=test_currency
            ),
            "status": OrderStatus.PENDING.value,
            "shipping_price": Money(
                amount=Decimal("10.00"), currency=test_currency
            ),
            "street": "123 Main St",
            "street_number": "Apt 4",
            "city": "Test City",
            "zipcode": "12345",
            "country": self.order.country,
            "region": self.order.region,
            "pay_way": self.order.pay_way,
        }
        self.items_data = [
            {
                "product": self.product,
                "quantity": 2,
            }
        ]

    def test_get_order_by_id(self):
        result = OrderService.get_order_by_id(self.order.id)

        self.assertEqual(result.id, self.order.id)

        result = OrderService.get_order_by_id(self.order.id)

        self.assertEqual(result.id, self.order.id)

    def test_get_order_by_uuid(self):
        result = OrderService.get_order_by_uuid(str(self.order.uuid))

        self.assertEqual(result.id, self.order.id)

        result = OrderService.get_order_by_uuid(str(self.order.uuid))

        self.assertEqual(result.id, self.order.id)

    @patch("order.signals.order_created.send")
    def test_create_order(self, mock_signal):
        initial_count = Order.objects.count()

        new_order = OrderService.create_order(
            order_data=self.order_data,
            items_data=self.items_data,
            user=self.user,
        )

        self.assertEqual(Order.objects.count(), initial_count + 1)

        self.assertEqual(new_order.email, self.order_data["email"])
        self.assertEqual(new_order.first_name, self.order_data["first_name"])

        self.assertEqual(new_order.items.count(), len(self.items_data))

        mock_signal.assert_called_once()

    def test_create_order_insufficient_stock(self):
        self.product.stock = 1
        self.product.save()

        with self.assertRaises(ValueError):
            OrderService.create_order(
                order_data=self.order_data,
                items_data=self.items_data,
                user=self.user,
            )

    def test_update_order_status_valid(self):
        with transaction.atomic():
            order = OrderFactory()
            order.status = OrderStatus.PENDING.value
            order.save(update_fields=["status"])

            order.refresh_from_db()
            self.assertEqual(order.status, OrderStatus.PENDING.value)

            updated_order = OrderService.update_order_status(
                order=order, new_status=OrderStatus.PROCESSING.value
            )

            self.assertEqual(updated_order.status, OrderStatus.PROCESSING.value)

            order.refresh_from_db()
            self.assertEqual(order.status, OrderStatus.PROCESSING.value)

            transaction.set_rollback(True)

    def test_update_order_status_invalid(self):
        with transaction.atomic():
            order = OrderFactory()
            order.status = OrderStatus.PENDING.value
            order.save(update_fields=["status"])

            order.refresh_from_db()
            self.assertEqual(order.status, OrderStatus.PENDING.value)

            with self.assertRaises(ValueError):
                OrderService.update_order_status(
                    order=order, new_status=OrderStatus.COMPLETED.value
                )

            order.refresh_from_db()
            self.assertEqual(order.status, OrderStatus.PENDING.value)

            transaction.set_rollback(True)

    def test_get_user_orders(self):
        with transaction.atomic():
            User = get_user_model()
            test_user = User.objects.create_user(
                username="testuser_orders",
                email="testuser_orders@example.com",
                password="password123",
            )

            order1 = OrderFactory.build(user=test_user)
            order1.save()
            order2 = OrderFactory.build(user=test_user)
            order2.save()

            other_user = User.objects.create_user(
                username="other_user",
                email="other_user@example.com",
                password="password123",
            )
            other_order = OrderFactory.build(user=other_user)
            other_order.save()

            self.assertEqual(Order.objects.filter(user=test_user).count(), 2)

            user_orders = OrderService.get_user_orders(test_user.id)

            self.assertEqual(user_orders.count(), 2)

            user_order_ids = [order.id for order in user_orders]
            self.assertIn(order1.id, user_order_ids)
            self.assertIn(order2.id, user_order_ids)

            self.assertNotIn(other_order.id, user_order_ids)

            transaction.set_rollback(True)

    @patch("order.signals.order_canceled.send")
    def test_cancel_order(self, mock_signal):
        order = OrderFactory()

        order.status = OrderStatus.PENDING.value
        order.save(update_fields=["status"])

        product = ProductFactory(stock=10)
        test_currency = order.shipping_price.currency
        order.items.create(
            product=product,
            price=Money(amount=Decimal("50.00"), currency=test_currency),
            quantity=3,
        )

        product.refresh_from_db()
        self.assertEqual(product.stock, 7)

        canceled_order, refund_info = OrderService.cancel_order(
            order=order,
            reason="Test cancellation",
            refund_payment=False,
            canceled_by=None,
        )

        self.assertEqual(canceled_order.status, OrderStatus.CANCELED.value)
        self.assertIsNone(refund_info)

        product.refresh_from_db()
        self.assertEqual(product.stock, 10)

    @patch("order.signals.handlers.order_canceled.send")
    def test_cancel_order_with_refund(self, mock_signal):
        order = OrderFactory()
        order.status = OrderStatus.PENDING.value
        order.payment_status = PaymentStatus.COMPLETED
        order.payment_id = "test_payment_123"

        test_currency = order.shipping_price.currency
        order.paid_amount = Money(
            amount=Decimal("100.00"), currency=test_currency
        )

        order.save(
            update_fields=[
                "status",
                "payment_status",
                "payment_id",
                "paid_amount",
            ]
        )

        product = ProductFactory(stock=10)
        order.items.create(
            product=product,
            price=Money(amount=Decimal("50.00"), currency=test_currency),
            quantity=2,
        )

        self.assertIsNotNone(order.pay_way, "Order must have a payment method")
        self.assertIsNotNone(order.payment_id, "Order must have a payment ID")
        self.assertTrue(order.is_paid, "Order must be marked as paid")

        with patch.object(OrderService, "refund_order") as mock_refund:
            mock_refund.return_value = (
                True,
                {"refund_id": "refund_123", "status": PaymentStatus.REFUNDED},
            )

            canceled_order, refund_info = OrderService.cancel_order(
                order=order,
                reason="Customer requested",
                refund_payment=True,
                canceled_by=order.user.id if order.user else None,
            )

            self.assertEqual(canceled_order.status, OrderStatus.CANCELED.value)
            self.assertIsNotNone(refund_info)
            self.assertTrue(refund_info["refunded"])
            self.assertIn("refund_id", refund_info)

            mock_refund.assert_called_once()

    def test_calculate_shipping_cost(self):
        order_value = Money(
            amount=Decimal("49.99"), currency=settings.DEFAULT_CURRENCY
        )
        shipping_cost = OrderService.calculate_shipping_cost(order_value)
        self.assertTrue(shipping_cost.amount > 0)

        order_value = Money(
            amount=Decimal("500.00"), currency=settings.DEFAULT_CURRENCY
        )
        shipping_cost = OrderService.calculate_shipping_cost(order_value)
        self.assertEqual(shipping_cost.amount, 0)

    @patch("order.payment.get_payment_provider")
    def test_refund_order(self, mock_get_provider):
        order = OrderFactory()
        order.payment_status = PaymentStatus.COMPLETED
        order.payment_id = "test_payment_123"
        test_currency = order.shipping_price.currency
        order.paid_amount = Money(
            amount=Decimal("100.00"), currency=test_currency
        )
        order.save()

        mock_provider = mock_get_provider.return_value
        mock_provider.refund_payment.return_value = (
            True,
            {
                "refund_id": "refund_123",
                "status": PaymentStatus.REFUNDED,
            },
        )

        success, response = OrderService.refund_order(
            order=order,
            amount=None,
            reason="Test refund",
            refunded_by=order.user.id if order.user else None,
        )

        self.assertTrue(success)
        self.assertIn("refund_id", response)
        self.assertEqual(order.payment_status, PaymentStatus.REFUNDED)

        self.assertIn("refunds", order.metadata)
        self.assertEqual(len(order.metadata["refunds"]), 1)
        self.assertEqual(order.metadata["refunds"][0]["amount"], "full")

    @patch("order.payment.get_payment_provider")
    def test_refund_order_partial(self, mock_get_provider):
        order = OrderFactory()
        order.payment_status = PaymentStatus.COMPLETED
        order.payment_id = "test_payment_123"
        test_currency = order.shipping_price.currency
        order.paid_amount = Money(
            amount=Decimal("100.00"), currency=test_currency
        )
        order.save()

        mock_provider = mock_get_provider.return_value
        mock_provider.refund_payment.return_value = (
            True,
            {
                "refund_id": "refund_456",
                "status": PaymentStatus.PARTIALLY_REFUNDED,
            },
        )

        refund_amount = Money(amount=Decimal("25.00"), currency=test_currency)
        success, response = OrderService.refund_order(
            order=order,
            amount=refund_amount,
            reason="Partial refund",
            refunded_by=order.user.id if order.user else None,
        )

        self.assertTrue(success)
        self.assertEqual(order.payment_status, PaymentStatus.PARTIALLY_REFUNDED)
        self.assertEqual(order.metadata["refunds"][0]["amount"], "25.00")

    def test_refund_order_not_paid(self):
        order = OrderFactory()
        order.payment_status = PaymentStatus.PENDING
        order.payment_id = "test_payment_123"
        order.save(update_fields=["payment_status", "payment_id"])

        with self.assertRaises(ValueError) as context:
            OrderService.refund_order(order=order)

        self.assertIn(
            "This order has not been paid yet.", str(context.exception)
        )

    @patch("order.payment.get_payment_provider")
    def test_get_payment_status(self, mock_get_provider):
        order = OrderFactory()
        order.payment_id = "test_payment_123"
        order.payment_status = PaymentStatus.PENDING
        order.save()

        mock_provider = mock_get_provider.return_value
        mock_provider.get_payment_status.return_value = (
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

    def test_add_tracking_info(self):
        order = OrderFactory()
        order.status = OrderStatus.PROCESSING.value
        order.save()

        updated_order = OrderService.add_tracking_info(
            order=order,
            tracking_number="TRACK123",
            shipping_carrier="DHL",
            auto_update_status=True,
        )

        self.assertEqual(updated_order.tracking_number, "TRACK123")
        self.assertEqual(updated_order.shipping_carrier, "DHL")
        self.assertEqual(updated_order.status, OrderStatus.SHIPPED.value)
