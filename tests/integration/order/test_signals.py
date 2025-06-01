from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from djmoney.money import Money

from order.enum.status_enum import OrderStatusEnum, PaymentStatusEnum
from order.factories.order import OrderFactory
from order.models.history import OrderHistory, OrderItemHistory
from order.models.order import Order
from order.signals import (
    order_canceled,
    order_completed,
    order_created,
    order_shipped,
    order_status_changed,
)
from product.factories.product import ProductFactory


class OrderSignalsTestCase(TestCase):
    def setUp(self):
        self.order = OrderFactory(status=OrderStatusEnum.PENDING.value)
        self.product = ProductFactory(stock=10)
        self.order_item = self.order.items.create(
            product=self.product,
            price=Money(amount=Decimal("50.00"), currency="USD"),
            quantity=2,
        )

        self.product.refresh_from_db()
        self.initial_stock = self.product.stock

    @patch("order.notifications.send_order_confirmation")
    @patch("order.tasks.send_order_confirmation_email.delay")
    def test_order_created_only_sends_one_email(
        self, mock_email_task, mock_direct_notification
    ):
        # Test that only the Celery task is called and not the direct notification
        order_created.send(sender=Order, order=self.order)

        mock_email_task.assert_called_once_with(self.order.id)
        mock_direct_notification.assert_not_called()

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="NOTE",
                new_value={"note": "Order created"},
            ).exists()
        )

    @patch("order.tasks.send_order_status_update_email.delay")
    def test_order_status_changed_signal(self, mock_email_task):
        old_status = OrderStatusEnum.PENDING.value
        new_status = OrderStatusEnum.PROCESSING.value

        order_status_changed.send(
            sender=Order,
            order=self.order,
            old_status=old_status,
            new_status=new_status,
        )

        mock_email_task.assert_called_once_with(self.order.id, new_status)

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="STATUS",
                previous_value={"status": old_status},
                new_value={"status": new_status},
            ).exists()
        )

    @patch("order.signals.order_paid.send")
    def test_order_status_changed_to_paid(self, mock_paid_signal):
        old_status = OrderStatusEnum.PENDING.value
        new_status = OrderStatusEnum.PROCESSING.value

        Order.objects.filter(id=self.order.id).update(
            payment_status=PaymentStatusEnum.COMPLETED
        )
        self.order.refresh_from_db()

        order_status_changed.send(
            sender=Order,
            order=self.order,
            old_status=old_status,
            new_status=new_status,
        )

        mock_paid_signal.assert_called_once()

    @patch("order.signals.order_canceled.send")
    def test_order_status_changed_to_canceled(self, mock_canceled_signal):
        old_status = OrderStatusEnum.PENDING.value
        new_status = OrderStatusEnum.CANCELED.value

        order_status_changed.send(
            sender=Order,
            order=self.order,
            old_status=old_status,
            new_status=new_status,
        )

        mock_canceled_signal.assert_called_once()

    @patch("order.signals.order_shipped.send")
    def test_order_status_changed_to_shipped(self, mock_shipped_signal):
        old_status = OrderStatusEnum.PROCESSING.value
        new_status = OrderStatusEnum.SHIPPED.value

        order_status_changed.send(
            sender=Order,
            order=self.order,
            old_status=old_status,
            new_status=new_status,
        )

        mock_shipped_signal.assert_called_once()

    def test_handle_order_saved(self):
        self.order._previous_status = OrderStatusEnum.PENDING.value

        self.order.status = OrderStatusEnum.PROCESSING.value

        with patch("order.signals.order_status_changed.send") as mock_signal:
            self.order.save()

            mock_signal.assert_called_once()

    def test_handle_order_item_pre_save(self):
        original_quantity = self.order_item.quantity
        new_quantity = original_quantity + 2

        self.order_item.quantity = new_quantity
        self.order_item.save()

        self.assertEqual(self.order_item._original_quantity, original_quantity)

    def test_handle_order_item_saved_quantity_changed(self):
        original_quantity = self.order_item.quantity
        new_quantity = original_quantity + 2
        self.order_item._original_quantity = original_quantity

        self.order_item.quantity = new_quantity

        self.order_item.save()

        self.product.refresh_from_db()

        self.assertEqual(self.product.stock, self.initial_stock - 2)

        self.assertTrue(
            OrderItemHistory.objects.filter(
                order_item=self.order_item,
                change_type="QUANTITY",
                previous_value={"quantity": original_quantity},
                new_value={"quantity": new_quantity},
            ).exists()
        )

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="ITEM_UPDATED",
                previous_value={"quantity": original_quantity},
                new_value={"quantity": new_quantity},
            ).exists()
        )

    def test_handle_order_item_saved_price_changed(self):
        original_price = self.order_item.price
        new_price = Money(amount=Decimal("60.00"), currency="USD")
        self.order_item._original_price = original_price

        self.order_item.price = new_price

        self.order_item.save()

        history = OrderItemHistory.objects.filter(
            order_item=self.order_item, change_type="PRICE"
        ).first()

        self.assertIsNotNone(history)
        self.assertEqual(
            history.previous_value["price"], float(original_price.amount)
        )
        self.assertEqual(history.new_value["price"], float(new_price.amount))

    @patch("order.tasks.send_shipping_notification_email.delay")
    def test_handle_order_shipped(self, mock_email_task):
        self.order.tracking_number = "TRACK123"
        self.order.shipping_carrier = "FedEx"
        self.order.status = OrderStatusEnum.PROCESSING.value
        self.order.save()

        order_shipped.send(sender=Order, order=self.order)

        mock_email_task.assert_called_with(self.order.id)

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order, change_type="SHIPPING"
            ).exists()
        )

    @patch("order.tasks.generate_order_invoice.delay")
    def test_handle_order_completed(self, mock_invoice_task):
        self.order.document_type = "INVOICE"
        self.order.save()

        order_completed.send(sender=Order, order=self.order)

        mock_invoice_task.assert_called_once_with(self.order.id)

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="NOTE",
                new_value={"note": "Order completed"},
            ).exists()
        )

    def test_handle_order_canceled(self):
        order_canceled.send(
            sender=Order, order=self.order, reason="Customer request"
        )

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="NOTE",
                new_value={"note": "Order canceled. Reason: Customer request"},
            ).exists()
        )

    def test_handle_order_refunded(self):
        from order.signals import order_refunded

        order_refunded.send(
            sender=Order,
            order=self.order,
            amount="$50.00",
            reason="Defective product",
        )

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="REFUND",
                new_value={"amount": "$50.00", "reason": "Defective product"},
            ).exists()
        )

    def test_handle_order_returned(self):
        from order.signals import order_returned

        return_items = [{"product_name": self.product.name, "quantity": 1}]

        order_returned.send(
            sender=Order,
            order=self.order,
            items=return_items,
            reason="Wrong size",
        )

        note_exists = OrderHistory.objects.filter(
            order=self.order,
            change_type="NOTE",
        ).exists()

        self.assertTrue(note_exists)
