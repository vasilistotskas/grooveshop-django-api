from unittest import TestCase

import pytest
from djmoney.money import Money

from order.enum.status import OrderStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models.history import OrderHistory, OrderItemHistory


@pytest.mark.django_db
class OrderHistoryTestCase(TestCase):
    def setUp(self):
        self.order = OrderFactory()
        self.user = None

        self.request = None

    def test_log_status_change(self):
        result = OrderHistory.log_status_change(
            order=self.order,
            previous_status=OrderStatus.PENDING,
            new_status=OrderStatus.PROCESSING,
            user=self.user,
            request=self.request,
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "STATUS")
        self.assertEqual(result.previous_value, {"status": OrderStatus.PENDING})
        self.assertEqual(result.new_value, {"status": OrderStatus.PROCESSING})
        self.assertIsNone(result.ip_address)
        self.assertEqual(result.user_agent, "")
        self.assertEqual(
            result.description,
            f"Status changed from {OrderStatus.PENDING} to {OrderStatus.PROCESSING}",
        )

    def test_log_payment_update(self):
        previous_value = {"paid_amount": {"amount": 0, "currency": "USD"}}
        new_value = {"paid_amount": {"amount": 100, "currency": "USD"}}

        result = OrderHistory.log_payment_update(
            order=self.order,
            previous_value=previous_value,
            new_value=new_value,
            user=self.user,
            request=self.request,
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "PAYMENT")
        self.assertEqual(result.previous_value, previous_value)
        self.assertEqual(result.new_value, new_value)
        self.assertIsNone(result.ip_address)
        self.assertEqual(result.user_agent, "")
        self.assertEqual(result.description, "Payment information updated")

    def test_log_payment_update_with_money_object(self):
        previous_value = {"paid_amount": Money("0.00", "USD")}
        new_value = {"paid_amount": Money("100.00", "USD")}

        result = OrderHistory.log_payment_update(
            order=self.order,
            previous_value=previous_value,
            new_value=new_value,
            user=self.user,
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "PAYMENT")
        self.assertEqual(
            result.previous_value["paid_amount"],
            str(Money("0.00", "USD")),
        )
        self.assertEqual(
            result.new_value["paid_amount"], str(Money("100.00", "USD"))
        )
        self.assertEqual(result.description, "Payment information updated")

    def test_log_shipping_update(self):
        previous_value = {"tracking_number": None, "carrier": None}
        new_value = {"tracking_number": "TRACK123", "carrier": "FedEx"}

        result = OrderHistory.log_shipping_update(
            order=self.order,
            previous_value=previous_value,
            new_value=new_value,
            user=self.user,
            request=self.request,
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "SHIPPING")
        self.assertEqual(result.previous_value, previous_value)
        self.assertEqual(result.new_value, new_value)
        self.assertIsNone(result.ip_address)
        self.assertEqual(result.user_agent, "")
        self.assertEqual(result.description, "Shipping information updated")

    def test_log_note(self):
        note = "This is a test note"

        result = OrderHistory.log_note(
            order=self.order, note=note, user=self.user, request=self.request
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "NOTE")
        self.assertEqual(result.new_value, {"note": note})
        self.assertIsNone(result.ip_address)
        self.assertEqual(result.user_agent, "")
        self.assertEqual(result.description, "Note added to order")

    def test_log_refund(self):
        refund_data = {
            "amount": {"amount": 50, "currency": "USD"},
            "reason": "Customer request",
            "transaction_id": "refund_123",
        }

        result = OrderHistory.log_refund(
            order=self.order,
            refund_data=refund_data,
            user=self.user,
            request=self.request,
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "REFUND")
        self.assertEqual(result.new_value, refund_data)
        self.assertIsNone(result.ip_address)
        self.assertEqual(result.user_agent, "")
        self.assertEqual(
            result.description, f"Refund processed for {refund_data['amount']}"
        )


@pytest.mark.django_db
class OrderItemHistoryTestCase(TestCase):
    def setUp(self):
        self.order_item = OrderItemFactory()
        self.user = None

    def test_log_quantity_change(self):
        result = OrderItemHistory.log_quantity_change(
            order_item=self.order_item,
            previous_quantity=1,
            new_quantity=2,
            user=self.user,
            reason="Customer request",
        )

        self.assertIsInstance(result, OrderItemHistory)
        self.assertEqual(result.order_item, self.order_item)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "QUANTITY")
        self.assertEqual(result.previous_value, {"quantity": 1})
        self.assertEqual(result.new_value, {"quantity": 2})
        self.assertEqual(
            result.description,
            "Quantity changed from 1 to 2. Reason: Customer request",
        )

    def test_log_price_update(self):
        previous_price = Money("50.00", "USD")
        new_price = Money("45.00", "USD")

        result = OrderItemHistory.log_price_update(
            order_item=self.order_item,
            previous_price=previous_price,
            new_price=new_price,
            user=self.user,
            reason="Price adjustment",
        )

        self.assertIsInstance(result, OrderItemHistory)
        self.assertEqual(result.order_item, self.order_item)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "PRICE")
        self.assertEqual(
            result.previous_value["price"], float(previous_price.amount)
        )
        self.assertEqual(
            result.previous_value["currency"], str(previous_price.currency)
        )
        self.assertEqual(result.new_value["price"], float(new_price.amount))
        self.assertEqual(result.new_value["currency"], str(new_price.currency))
        self.assertIn("Price updated from", result.description)
        self.assertIn("Price adjustment", result.description)

    def test_log_refund(self):
        result = OrderItemHistory.log_refund(
            order_item=self.order_item,
            refund_quantity=1,
            user=self.user,
            reason="Damaged item",
        )

        self.assertIsInstance(result, OrderItemHistory)
        self.assertEqual(result.order_item, self.order_item)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "REFUND")
        self.assertEqual(result.new_value["refund_quantity"], 1)
        self.assertIn("refund_amount", result.new_value)
        self.assertEqual(result.new_value["currency"], "USD")
        self.assertIn("Refund processed for 1 items", result.description)
        self.assertIn("Damaged item", result.description)
