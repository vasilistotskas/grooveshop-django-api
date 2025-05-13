from unittest import TestCase
from unittest.mock import Mock, patch

from django.http import HttpRequest
from djmoney.money import Money

from order.enum.status_enum import OrderStatusEnum
from order.models.history import OrderHistory, OrderItemHistory


class OrderHistoryTestCase(TestCase):
    def setUp(self):
        self.order = Mock()
        self.order.id = 1

        self.user = Mock()
        self.user.id = 42

        self.request = Mock(spec=HttpRequest)
        self.request.META = {
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_USER_AGENT": "Mozilla/5.0 (Test)",
        }

    @patch("order.models.history.OrderHistory.objects.create")
    def test_log_status_change(self, mock_create):
        history_entry = Mock(spec=OrderHistory)
        mock_create.return_value = history_entry

        result = OrderHistory.log_status_change(
            order=self.order,
            previous_status=OrderStatusEnum.PENDING,
            new_status=OrderStatusEnum.PROCESSING,
            user=self.user,
            request=self.request,
        )

        self.assertEqual(result, history_entry)

        mock_create.assert_called_once_with(
            order=self.order,
            user=self.user,
            change_type="STATUS",
            previous_value={"status": OrderStatusEnum.PENDING},
            new_value={"status": OrderStatusEnum.PROCESSING},
            description=f"Status changed from {OrderStatusEnum.PENDING} to {OrderStatusEnum.PROCESSING}",
            ip_address="127.0.0.1",
            user_agent="Mozilla/5.0 (Test)",
        )

    @patch("order.models.history.OrderHistory.objects.create")
    def test_log_payment_update(self, mock_create):
        previous_value = {"paid_amount": {"amount": 0, "currency": "USD"}}
        new_value = {"paid_amount": {"amount": 100, "currency": "USD"}}

        history_entry = Mock(spec=OrderHistory)
        mock_create.return_value = history_entry

        result = OrderHistory.log_payment_update(
            order=self.order,
            previous_value=previous_value,
            new_value=new_value,
            user=self.user,
            request=self.request,
        )

        self.assertEqual(result, history_entry)

        mock_create.assert_called_once_with(
            order=self.order,
            user=self.user,
            change_type="PAYMENT",
            previous_value=previous_value,
            new_value=new_value,
            description="Payment information updated",
            ip_address="127.0.0.1",
            user_agent="Mozilla/5.0 (Test)",
        )

    @patch("order.models.history.OrderHistory.objects.create")
    def test_log_payment_update_with_money_object(self, mock_create):
        previous_value = {"paid_amount": Money("0.00", "USD")}
        new_value = {"paid_amount": Money("100.00", "USD")}

        history_entry = Mock(spec=OrderHistory)
        mock_create.return_value = history_entry

        result = OrderHistory.log_payment_update(
            order=self.order,
            previous_value=previous_value,
            new_value=new_value,
            user=self.user,
        )

        self.assertEqual(result, history_entry)

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["order"], self.order)
        self.assertEqual(call_kwargs["user"], self.user)
        self.assertEqual(call_kwargs["change_type"], "PAYMENT")
        self.assertEqual(
            call_kwargs["previous_value"]["paid_amount"],
            str(Money("0.00", "USD")),
        )
        self.assertEqual(
            call_kwargs["new_value"]["paid_amount"], str(Money("100.00", "USD"))
        )

    @patch("order.models.history.OrderHistory.objects.create")
    def test_log_shipping_update(self, mock_create):
        previous_value = {"tracking_number": None, "carrier": None}
        new_value = {"tracking_number": "TRACK123", "carrier": "FedEx"}

        history_entry = Mock(spec=OrderHistory)
        mock_create.return_value = history_entry

        result = OrderHistory.log_shipping_update(
            order=self.order,
            previous_value=previous_value,
            new_value=new_value,
            user=self.user,
            request=self.request,
        )

        self.assertEqual(result, history_entry)

        mock_create.assert_called_once_with(
            order=self.order,
            user=self.user,
            change_type="SHIPPING",
            previous_value=previous_value,
            new_value=new_value,
            description="Shipping information updated",
            ip_address="127.0.0.1",
            user_agent="Mozilla/5.0 (Test)",
        )

    @patch("order.models.history.OrderHistory.objects.create")
    def test_log_note(self, mock_create):
        note = "This is a test note"

        history_entry = Mock(spec=OrderHistory)
        mock_create.return_value = history_entry

        result = OrderHistory.log_note(
            order=self.order, note=note, user=self.user, request=self.request
        )

        self.assertEqual(result, history_entry)

        mock_create.assert_called_once_with(
            order=self.order,
            user=self.user,
            change_type="NOTE",
            new_value={"note": note},
            description="Note added to order",
            ip_address="127.0.0.1",
            user_agent="Mozilla/5.0 (Test)",
        )

    @patch("order.models.history.OrderHistory.objects.create")
    def test_log_refund(self, mock_create):
        refund_data = {
            "amount": {"amount": 50, "currency": "USD"},
            "reason": "Customer request",
            "transaction_id": "refund_123",
        }

        history_entry = Mock(spec=OrderHistory)
        mock_create.return_value = history_entry

        result = OrderHistory.log_refund(
            order=self.order,
            refund_data=refund_data,
            user=self.user,
            request=self.request,
        )

        self.assertEqual(result, history_entry)

        mock_create.assert_called_once_with(
            order=self.order,
            user=self.user,
            change_type="REFUND",
            new_value=refund_data,
            description=f"Refund processed for {refund_data['amount']}",
            ip_address="127.0.0.1",
            user_agent="Mozilla/5.0 (Test)",
        )


class OrderItemHistoryTestCase(TestCase):
    def setUp(self):
        self.order_item = Mock()
        self.order_item.id = 1
        self.order_item.product = Mock()
        self.order_item.product.name = "Test Product"

        self.user = Mock()
        self.user.id = 42

    @patch("order.models.history.OrderItemHistory.objects.create")
    def test_log_quantity_change(self, mock_create):
        history_entry = Mock(spec=OrderItemHistory)
        mock_create.return_value = history_entry

        result = OrderItemHistory.log_quantity_change(
            order_item=self.order_item,
            previous_quantity=1,
            new_quantity=2,
            user=self.user,
            reason="Customer request",
        )

        self.assertEqual(result, history_entry)

        mock_create.assert_called_once_with(
            order_item=self.order_item,
            user=self.user,
            change_type="QUANTITY",
            previous_value={"quantity": 1},
            new_value={"quantity": 2},
            description="Quantity changed from 1 to 2. Reason: Customer request",
        )

    @patch("order.models.history.OrderItemHistory.objects.create")
    def test_log_price_update(self, mock_create):
        previous_price = Money("50.00", "USD")
        new_price = Money("45.00", "USD")

        history_entry = Mock(spec=OrderItemHistory)
        mock_create.return_value = history_entry

        result = OrderItemHistory.log_price_update(
            order_item=self.order_item,
            previous_price=previous_price,
            new_price=new_price,
            user=self.user,
            reason="Price adjustment",
        )

        self.assertEqual(result, history_entry)

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["order_item"], self.order_item)
        self.assertEqual(call_kwargs["user"], self.user)
        self.assertEqual(call_kwargs["change_type"], "PRICE")
        self.assertIn("Price updated from", call_kwargs["description"])

    @patch("order.models.history.OrderItemHistory.objects.create")
    def test_log_refund(self, mock_create):
        self.order_item.price = Money("50.00", "USD")

        history_entry = Mock(spec=OrderItemHistory)
        mock_create.return_value = history_entry

        result = OrderItemHistory.log_refund(
            order_item=self.order_item,
            refund_quantity=1,
            user=self.user,
            reason="Damaged item",
        )

        self.assertEqual(result, history_entry)

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["order_item"], self.order_item)
        self.assertEqual(call_kwargs["user"], self.user)
        self.assertEqual(call_kwargs["change_type"], "REFUND")
        self.assertEqual(call_kwargs["new_value"]["refund_quantity"], 1)
        self.assertIn("refund_amount", call_kwargs["new_value"])
