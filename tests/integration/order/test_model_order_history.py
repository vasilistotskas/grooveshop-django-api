from decimal import Decimal

from django.test import TestCase
from djmoney.money import Money

from order.factories.order import OrderFactory
from order.models.history import OrderHistory, OrderItemHistory
from product.factories.product import ProductFactory


class OrderHistoryModelTestCase(TestCase):
    def setUp(self):
        self.order = OrderFactory()

    def test_log_status_change(self):
        previous_status = "PENDING"
        new_status = "PROCESSING"

        history_entry = OrderHistory.log_status_change(
            order=self.order,
            previous_status=previous_status,
            new_status=new_status,
        )

        self.assertEqual(history_entry.order, self.order)
        self.assertEqual(history_entry.change_type, "STATUS")
        self.assertEqual(
            history_entry.previous_value, {"status": previous_status}
        )
        self.assertEqual(history_entry.new_value, {"status": new_status})
        self.assertIn(
            f"Status changed from {previous_status} to {new_status}",
            history_entry.description,
        )

    def test_log_payment_update(self):
        previous_value = {"payment_id": "prev123", "status": "pending"}
        new_value = {"payment_id": "new456", "status": "completed"}

        history_entry = OrderHistory.log_payment_update(
            order=self.order, previous_value=previous_value, new_value=new_value
        )

        self.assertEqual(history_entry.order, self.order)
        self.assertEqual(history_entry.change_type, "PAYMENT")
        self.assertEqual(history_entry.previous_value, previous_value)
        self.assertEqual(history_entry.new_value, new_value)
        self.assertIn("Payment information updated", history_entry.description)

    def test_log_shipping_update(self):
        previous_value = None
        new_value = {
            "tracking_number": "TRACK123",
            "carrier": "FedEx",
            "shipped_at": "2023-05-10T12:00:00Z",
        }

        history_entry = OrderHistory.log_shipping_update(
            order=self.order, previous_value=previous_value, new_value=new_value
        )

        self.assertEqual(history_entry.order, self.order)
        self.assertEqual(history_entry.change_type, "SHIPPING")
        self.assertEqual(history_entry.new_value, new_value)
        self.assertIn("Shipping information updated", history_entry.description)

    def test_log_note(self):
        note = "This is a test note about the order"

        history_entry = OrderHistory.log_note(order=self.order, note=note)

        self.assertEqual(history_entry.order, self.order)
        self.assertEqual(history_entry.change_type, "NOTE")
        self.assertEqual(history_entry.new_value, {"note": note})
        self.assertIn("Note added to order", history_entry.description)

    def test_log_refund(self):
        refund_data = {
            "amount": "50.00",
            "currency": "USD",
            "reason": "Customer request",
        }

        history_entry = OrderHistory.log_refund(
            order=self.order, refund_data=refund_data
        )

        self.assertEqual(history_entry.order, self.order)
        self.assertEqual(history_entry.change_type, "REFUND")
        self.assertEqual(history_entry.new_value, refund_data)
        self.assertIn("Refund processed for ", history_entry.description)


class OrderItemHistoryModelTestCase(TestCase):
    def setUp(self):
        self.order = OrderFactory()
        self.product = ProductFactory()
        self.order_item = self.order.items.create(
            product=self.product,
            price=Money(amount=Decimal("50.00"), currency="USD"),
            quantity=2,
        )

    def test_log_quantity_change(self):
        previous_quantity = 2
        new_quantity = 3

        history_entry = OrderItemHistory.log_quantity_change(
            order_item=self.order_item,
            previous_quantity=previous_quantity,
            new_quantity=new_quantity,
        )

        self.assertEqual(history_entry.order_item, self.order_item)
        self.assertEqual(history_entry.change_type, "QUANTITY")
        self.assertEqual(
            history_entry.previous_value, {"quantity": previous_quantity}
        )
        self.assertEqual(history_entry.new_value, {"quantity": new_quantity})
        self.assertIn(
            f"Quantity changed from {previous_quantity} to {new_quantity}",
            history_entry.description,
        )

    def test_log_price_update(self):
        previous_price = Money(amount=Decimal("50.00"), currency="USD")
        new_price = Money(amount=Decimal("60.00"), currency="USD")

        history_entry = OrderItemHistory.log_price_update(
            order_item=self.order_item,
            previous_price=previous_price,
            new_price=new_price,
        )

        self.assertEqual(history_entry.order_item, self.order_item)
        self.assertEqual(history_entry.change_type, "PRICE")
        self.assertEqual(
            history_entry.previous_value["price"], float(previous_price.amount)
        )
        self.assertEqual(
            history_entry.new_value["price"], float(new_price.amount)
        )
        self.assertIn(
            f"Price updated from {previous_price} to {new_price}",
            history_entry.description,
        )

    def test_log_refund(self):
        refund_quantity = 1

        history_entry = OrderItemHistory.log_refund(
            order_item=self.order_item, refund_quantity=refund_quantity
        )

        self.assertEqual(history_entry.order_item, self.order_item)
        self.assertEqual(history_entry.change_type, "REFUND")
        self.assertEqual(
            history_entry.new_value["refund_quantity"], refund_quantity
        )
        self.assertIn(
            f"Refund processed for {refund_quantity} items",
            history_entry.description,
        )
