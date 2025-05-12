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
    """Test case for order signals."""

    def setUp(self):
        """Set up test data."""
        self.order = OrderFactory(status=OrderStatusEnum.PENDING.value)
        self.product = ProductFactory(stock=10)
        self.order_item = self.order.items.create(
            product=self.product,
            price=Money(amount=Decimal("50.00"), currency="USD"),
            quantity=2,
        )

        # Refresh the product to get the updated stock
        self.product.refresh_from_db()
        self.initial_stock = self.product.stock

    @patch("order.tasks.send_order_confirmation_email.delay")
    def test_order_created_signal(self, mock_email_task):
        """Test order_created signal."""
        # Send the signal
        order_created.send(sender=Order, order=self.order)

        # Check that the email task was called
        mock_email_task.assert_called_once_with(self.order.id)

        # Check that history was created
        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="NOTE",
                new_value={"note": "Order created"},
            ).exists()
        )

    @patch("order.tasks.send_order_status_update_email.delay")
    def test_order_status_changed_signal(self, mock_email_task):
        """Test order_status_changed signal."""
        old_status = OrderStatusEnum.PENDING.value
        new_status = OrderStatusEnum.PROCESSING.value

        # Send the signal
        order_status_changed.send(
            sender=Order,
            order=self.order,
            old_status=old_status,
            new_status=new_status,
        )

        # Check that the email task was called
        mock_email_task.assert_called_once_with(self.order.id, new_status)

        # Check that history was created
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
        """Test order_status_changed signal when status changes to PAID."""
        old_status = OrderStatusEnum.PENDING.value
        new_status = OrderStatusEnum.PROCESSING.value

        # Set order as paid by updating the model directly
        Order.objects.filter(id=self.order.id).update(
            payment_status=PaymentStatusEnum.COMPLETED
        )
        self.order.refresh_from_db()

        # Send the signal
        order_status_changed.send(
            sender=Order,
            order=self.order,
            old_status=old_status,
            new_status=new_status,
        )

        # Check that the paid signal was sent
        mock_paid_signal.assert_called_once()

    @patch("order.signals.order_canceled.send")
    def test_order_status_changed_to_canceled(self, mock_canceled_signal):
        """Test order_status_changed signal when status changes to CANCELED."""
        old_status = OrderStatusEnum.PENDING.value
        new_status = OrderStatusEnum.CANCELED.value

        # Send the signal
        order_status_changed.send(
            sender=Order,
            order=self.order,
            old_status=old_status,
            new_status=new_status,
        )

        # Check that the canceled signal was sent
        mock_canceled_signal.assert_called_once()

    @patch("order.signals.order_shipped.send")
    def test_order_status_changed_to_shipped(self, mock_shipped_signal):
        """Test order_status_changed signal when status changes to SHIPPED."""
        old_status = OrderStatusEnum.PROCESSING.value
        new_status = OrderStatusEnum.SHIPPED.value

        # Send the signal
        order_status_changed.send(
            sender=Order,
            order=self.order,
            old_status=old_status,
            new_status=new_status,
        )

        # Check that the shipped signal was sent
        mock_shipped_signal.assert_called_once()

    def test_handle_order_saved(self):
        """Test the post_save signal handler for Order."""
        # Set the original status (usually done by pre_save)
        self.order._previous_status = OrderStatusEnum.PENDING.value

        # Change the status
        self.order.status = OrderStatusEnum.PROCESSING.value

        # Mock the order_status_changed signal
        with patch("order.signals.order_status_changed.send") as mock_signal:
            # Save the order
            self.order.save()

            # Check that the signal was sent
            mock_signal.assert_called_once()

    def test_handle_order_item_pre_save(self):
        """Test the pre_save signal handler for OrderItem."""
        # Change the quantity
        original_quantity = self.order_item.quantity
        new_quantity = original_quantity + 2

        # Save the item
        self.order_item.quantity = new_quantity
        self.order_item.save()

        # Check that the original quantity was stored
        self.assertEqual(self.order_item._original_quantity, original_quantity)

    def test_handle_order_item_saved_quantity_changed(self):
        """Test the post_save signal handler for OrderItem when quantity changes."""
        # Set up original values
        original_quantity = self.order_item.quantity
        new_quantity = original_quantity + 2
        self.order_item._original_quantity = original_quantity

        # Change the quantity
        self.order_item.quantity = new_quantity

        # Save the item
        self.order_item.save()

        # Refresh the product
        self.product.refresh_from_db()

        # Check that stock was updated
        self.assertEqual(self.product.stock, self.initial_stock - 2)

        # Check that order item history was created
        self.assertTrue(
            OrderItemHistory.objects.filter(
                order_item=self.order_item,
                change_type="QUANTITY",
                previous_value={"quantity": original_quantity},
                new_value={"quantity": new_quantity},
            ).exists()
        )

        # Check that order history was created
        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="ITEM_UPDATED",
                previous_value={"quantity": original_quantity},
                new_value={"quantity": new_quantity},
            ).exists()
        )

    def test_handle_order_item_saved_price_changed(self):
        """Test the post_save signal handler for OrderItem when price changes."""
        # Set up original values
        original_price = self.order_item.price
        new_price = Money(amount=Decimal("60.00"), currency="USD")
        self.order_item._original_price = original_price

        # Change the price
        self.order_item.price = new_price

        # Save the item
        self.order_item.save()

        # Check that history was created
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
        """Test the order_shipped signal handler."""
        # Set up shipping information
        self.order.tracking_number = "TRACK123"
        self.order.shipping_carrier = "FedEx"
        # Set order to processing first so we can ship it
        self.order.status = OrderStatusEnum.PROCESSING.value
        self.order.save()

        # Send the signal
        order_shipped.send(sender=Order, order=self.order)

        # Check that the email task was called
        mock_email_task.assert_called_with(self.order.id)

        # Check that history was created
        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order, change_type="SHIPPING"
            ).exists()
        )

    @patch("order.tasks.generate_order_invoice.delay")
    def test_handle_order_completed(self, mock_invoice_task):
        """Test the order_completed signal handler."""
        # Set document type to INVOICE
        self.order.document_type = "INVOICE"
        self.order.save()

        # Send the signal
        order_completed.send(sender=Order, order=self.order)

        # Check that the invoice task was called
        mock_invoice_task.assert_called_once_with(self.order.id)

        # Check that history note was created
        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="NOTE",
                new_value={"note": "Order completed"},
            ).exists()
        )

    def test_handle_order_canceled(self):
        """Test the order_canceled signal handler."""
        # Send the signal with a reason
        order_canceled.send(
            sender=Order, order=self.order, reason="Customer request"
        )

        # Check that history note was created
        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="NOTE",
                new_value={"note": "Order canceled. Reason: Customer request"},
            ).exists()
        )

    def test_handle_order_refunded(self):
        """Test the order_refunded signal handler."""
        from order.signals import order_refunded

        # Send the signal with amount and reason
        order_refunded.send(
            sender=Order,
            order=self.order,
            amount="$50.00",
            reason="Defective product",
        )

        # Check that refund was logged
        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="REFUND",
                new_value={"amount": "$50.00", "reason": "Defective product"},
            ).exists()
        )

    def test_handle_order_returned(self):
        """Test the order_returned signal handler."""
        from order.signals import order_returned

        # Create return items data
        return_items = [{"product_name": self.product.name, "quantity": 1}]

        # Send the signal with items and reason
        order_returned.send(
            sender=Order,
            order=self.order,
            items=return_items,
            reason="Wrong size",
        )

        # Check that note was created
        note_exists = OrderHistory.objects.filter(
            order=self.order,
            change_type="NOTE",
        ).exists()

        self.assertTrue(note_exists)
