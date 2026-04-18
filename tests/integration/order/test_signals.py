from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.history import OrderHistory, OrderItemHistory
from order.models.order import Order
from pay_way.factories import PayWayFactory
from order.signals import (
    order_canceled,
    order_completed,
    order_created,
    order_refunded,
    order_returned,
    order_shipped,
    order_status_changed,
)
from product.factories.product import ProductFactory


class OrderSignalsTestCase(TestCase):
    def setUp(self):
        self.order = OrderFactory(status=OrderStatus.PENDING.value)
        self.product = ProductFactory(stock=10)
        self.order_item = self.order.items.create(
            product=self.product,
            price=Money(
                amount=Decimal("50.00"), currency=settings.DEFAULT_CURRENCY
            ),
            quantity=2,
        )

        self.product.refresh_from_db()
        self.initial_stock = self.product.stock

    @patch("order.signals.handlers.send_order_confirmation_email.delay")
    def test_order_created_offline_payment_sends_email(self, mock_email_task):
        # Offline payment methods (COD, bank transfer) need the
        # confirmation email immediately so the customer has the order
        # details before paying manually.
        self.order.pay_way = PayWayFactory.create_offline_payment()
        self.order.save(update_fields=["pay_way"])

        order_created.send(sender=Order, order=self.order)

        mock_email_task.assert_called_once_with(self.order.id)

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="NOTE",
                new_value={"note": "Order created"},
            ).exists()
        )

    @patch("order.signals.handlers.send_order_confirmation_email.delay")
    def test_order_created_online_payment_defers_email(self, mock_email_task):
        # Online payments (Stripe, Viva) must defer the confirmation
        # email to the payment-success webhook — the customer should
        # only get the email once the payment actually clears.
        self.order.pay_way = PayWayFactory.create_online_payment(
            provider_code="stripe"
        )
        self.order.payment_status = PaymentStatus.PENDING
        self.order.save(update_fields=["pay_way", "payment_status"])

        order_created.send(sender=Order, order=self.order)

        mock_email_task.assert_not_called()

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="NOTE",
                new_value={"note": "Order created"},
            ).exists()
        )

    @patch("order.signals.handlers.send_order_confirmation_email.delay")
    def test_order_created_online_payment_already_paid_sends_email(
        self, mock_email_task
    ):
        # Safety net: if an online-payment order is somehow already
        # paid at creation time, we still send the email immediately.
        self.order.pay_way = PayWayFactory.create_online_payment(
            provider_code="stripe"
        )
        self.order.payment_status = PaymentStatus.COMPLETED
        self.order.save(update_fields=["pay_way", "payment_status"])

        order_created.send(sender=Order, order=self.order)

        mock_email_task.assert_called_once_with(self.order.id)

    @patch("order.tasks.send_order_status_update_email.delay")
    def test_order_status_changed_signal(self, mock_email_task):
        old_status = OrderStatus.PENDING.value
        new_status = OrderStatus.PROCESSING.value

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

    def test_order_status_changed_to_paid(self):
        old_status = OrderStatus.PENDING.value
        new_status = OrderStatus.PROCESSING.value

        Order.objects.filter(id=self.order.id).update(
            payment_status=PaymentStatus.COMPLETED
        )
        self.order.refresh_from_db()

        with patch(
            "order.tasks.send_order_confirmation_email.delay"
        ) as _mock_task:
            order_status_changed.send(
                sender=Order,
                order=self.order,
                old_status=old_status,
                new_status=new_status,
            )

    def test_order_status_changed_to_canceled(self):
        old_status = OrderStatus.PENDING.value
        new_status = OrderStatus.CANCELED.value

        with patch(
            "order.tasks.send_order_status_update_email.delay"
        ) as _mock_task:
            order_status_changed.send(
                sender=Order,
                order=self.order,
                old_status=old_status,
                new_status=new_status,
            )

    def test_order_status_changed_to_shipped(self):
        self.order.status = OrderStatus.PROCESSING
        self.order.save()

        old_status = OrderStatus.PROCESSING.value
        new_status = OrderStatus.SHIPPED.value

        with patch(
            "order.tasks.send_shipping_notification_email.delay"
        ) as _mock_task:
            order_status_changed.send(
                sender=Order,
                order=self.order,
                old_status=old_status,
                new_status=new_status,
            )

    def test_handle_order_saved(self):
        self.order._previous_status = OrderStatus.PENDING.value

        self.order.status = OrderStatus.PROCESSING.value

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

        quantity_history_exists = OrderHistory.objects.filter(
            order=self.order, new_value__contains="quantity"
        ).exists()

        if not quantity_history_exists:
            pass
        else:
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
        new_price = Money(
            amount=Decimal("60.00"), currency=settings.DEFAULT_CURRENCY
        )
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

    def test_handle_order_shipped(self):
        self.order.tracking_number = "TRACK123"
        self.order.shipping_carrier = "FedEx"
        self.order.status = OrderStatus.PROCESSING.value
        self.order.save()

        order_shipped.send(sender=Order, order=self.order)

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order, change_type="SHIPPING"
            ).exists()
        )

    def test_handle_order_completed(self):
        self.order.document_type = "INVOICE"
        self.order.save()

        order_completed.send(sender=Order, order=self.order)

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
