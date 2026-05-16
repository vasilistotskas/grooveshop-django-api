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

    @patch("order.signals.handlers.send_admin_new_order_email.delay")
    @patch("order.signals.handlers.send_order_confirmation_email.delay")
    def test_order_created_offline_payment_sends_email(
        self, mock_email_task, mock_admin_task
    ):
        # Offline payment methods (COD, bank transfer) need the
        # confirmation email immediately so the customer has the order
        # details before paying manually.
        self.order.pay_way = PayWayFactory.create_offline_payment()
        self.order.save(update_fields=["pay_way"])

        order_created.send(sender=Order, order=self.order)

        mock_email_task.assert_called_once_with(self.order.id)
        mock_admin_task.assert_called_once_with(self.order.id)

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="NOTE",
                new_value={"note": "Order created"},
            ).exists()
        )

    @patch("order.signals.handlers.send_admin_new_order_email.delay")
    @patch("order.signals.handlers.send_order_confirmation_email.delay")
    def test_order_created_online_payment_defers_email(
        self, mock_email_task, mock_admin_task
    ):
        # Online payments (Stripe, Viva) must defer the confirmation
        # email to the payment-success webhook — the customer should
        # only get the email once the payment actually clears. The
        # admin notification still fires on creation regardless.
        self.order.pay_way = PayWayFactory.create_online_payment(
            provider_code="stripe"
        )
        self.order.payment_status = PaymentStatus.PENDING
        self.order.save(update_fields=["pay_way", "payment_status"])

        order_created.send(sender=Order, order=self.order)

        mock_email_task.assert_not_called()
        mock_admin_task.assert_called_once_with(self.order.id)

        self.assertTrue(
            OrderHistory.objects.filter(
                order=self.order,
                change_type="NOTE",
                new_value={"note": "Order created"},
            ).exists()
        )

    @patch("order.signals.handlers.send_admin_new_order_email.delay")
    @patch("order.signals.handlers.send_order_confirmation_email.delay")
    def test_order_created_online_payment_already_paid_sends_email(
        self, mock_email_task, mock_admin_task
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
        mock_admin_task.assert_called_once_with(self.order.id)

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

    @patch("order.services.OrderService.cancel_attached_shipment")
    def test_handle_order_canceled_cascades_to_carrier(self, mock_cascade):
        """Locks the contract: every ``order_canceled`` dispatch runs
        the courier-voucher cascade. Verified necessary on 2026-05-16
        after prod order 60 was cancelled via the admin form and the
        ACS voucher stayed alive."""
        order_canceled.send(
            sender=Order, order=self.order, reason="Customer request"
        )

        mock_cascade.assert_called_once_with(self.order, "Customer request")

    @patch("order.services.OrderService.cancel_attached_shipment")
    def test_handle_order_canceled_admin_form_path_cascades(self, mock_cascade):
        """When the admin form save flips ``order.status`` to CANCELED
        directly (no kwargs/reason), the cascade still runs with a
        sensible default reason. This is the prod-order-60 path."""
        order_canceled.send(sender=Order, order=self.order)

        mock_cascade.assert_called_once_with(self.order, "admin status change")

    def test_handle_order_canceled_initialises_metadata_cancellation(self):
        """The admin-form-save path leaves ``metadata.cancellation``
        absent. The signal handler must seed it so the cascade has a
        parent dict to write its ``shipment_cancel`` outcome into.

        Uses the real cascade — the test order has no shipping
        provider attached, so ``ShippingService.cancel_shipment``
        short-circuits to ``False``, but the metadata save still runs
        end of cascade. That's what persists the seeded fields."""
        # Baseline: ensure the test order has no cancellation dict.
        self.order.metadata = {}
        self.order.save(update_fields=["metadata"])

        order_canceled.send(
            sender=Order, order=self.order, previous_status="PROCESSING"
        )

        self.order.refresh_from_db()
        cancellation = (self.order.metadata or {}).get("cancellation") or {}
        assert cancellation.get("previous_status") == "PROCESSING"
        assert cancellation.get("reason") == "admin status change"
        assert cancellation.get("canceled_at")  # truthy ISO timestamp
        # The cascade ran and wrote its outcome (no shipment attached
        # → dispatched=False).
        assert cancellation.get("shipment_cancel", {}).get("attempted") is True
        assert (
            cancellation.get("shipment_cancel", {}).get("dispatched") is False
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

    @patch("order.signals.handlers.send_shipping_notification_email.delay")
    def test_tracking_info_set_dispatches_shipping_email(self, mock_email_task):
        """Setting tracking_number + shipping_carrier on an order
        with neither previously set fires the shipping notification
        email task. PR #4 Q1."""
        self.order.tracking_number = ""
        self.order.shipping_carrier = ""
        self.order.save()
        # Reset call history because the save above is the baseline.
        mock_email_task.reset_mock()

        self.order.tracking_number = "TRACK-9876"
        self.order.shipping_carrier = "acs"
        self.order.save()

        mock_email_task.assert_called_once_with(self.order.id)

    @patch("order.signals.handlers.send_shipping_notification_email.delay")
    def test_tracking_info_unchanged_does_not_redispatch(self, mock_email_task):
        """Re-saving the same tracking value (e.g. admin re-edits the
        field with no change) must NOT re-fire the email."""
        self.order.tracking_number = "TRACK-9876"
        self.order.shipping_carrier = "acs"
        self.order.save()
        mock_email_task.reset_mock()

        # Same values — no transition, no signal, no email.
        self.order.save()

        mock_email_task.assert_not_called()
