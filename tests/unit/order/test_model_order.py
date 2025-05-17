from datetime import timedelta
from unittest import TestCase
from unittest.mock import MagicMock, Mock, PropertyMock, patch

from django.core.exceptions import ValidationError
from django.utils import timezone
from djmoney.money import Money

from order.enum.document_type_enum import OrderDocumentTypeEnum
from order.enum.status_enum import OrderStatusEnum, PaymentStatusEnum
from order.models.order import Order, OrderManager, OrderQuerySet


class OrderModelTestCase(TestCase):
    def setUp(self):
        self.order = Mock(spec=Order)
        self.order.id = 1
        self.order.uuid = "test-uuid-1234"
        self.order.email = "customer@example.com"
        self.order.first_name = "John"
        self.order.last_name = "Doe"
        self.order.status = OrderStatusEnum.PENDING
        self.order.created_at = timezone.now()
        self.order.document_type = OrderDocumentTypeEnum.RECEIPT
        self.order.shipping_price = Money("10.00", "USD")
        self.order.paid_amount = Money("0.00", "USD")
        self.order._original_status = OrderStatusEnum.PENDING

        self.order.street = "Test Street"
        self.order.street_number = "123"
        self.order.city = "Test City"
        self.order.zipcode = "12345"
        self.order.place = ""

        item1 = Mock()
        item1.product = Mock()
        item1.product.name = "Test Product 1"
        item1.quantity = 2
        item1.price = Money("50.00", "USD")
        item1.total = Money("100.00", "USD")

        item2 = Mock()
        item2.product = Mock()
        item2.product.name = "Test Product 2"
        item2.quantity = 1
        item2.price = Money("30.00", "USD")
        item2.total = Money("30.00", "USD")

        self.order.items = MagicMock()
        self.order.items.all.return_value = [item1, item2]

    def test_str_representation(self):
        order_str = Order.__str__(self.order)

        self.assertEqual(
            order_str,
            f"Order {self.order.id} - {self.order.first_name} {self.order.last_name}",
        )

    @patch("django.utils.timezone.now")
    def test_save_status_change(self, mock_timezone_now):
        mock_now = timezone.now()
        mock_timezone_now.return_value = mock_now

        self.order.status = OrderStatusEnum.PROCESSING

        with patch.object(Order, "save", lambda self, *args, **kwargs: None):
            Order.save(self.order)
            self.order.status_updated_at = mock_now

        self.assertEqual(self.order.status_updated_at, mock_now)

    def test_save_status_unchanged(self):
        self.order.status = OrderStatusEnum.PENDING
        self.order._original_status = OrderStatusEnum.PENDING

        previous_update = timezone.now() - timedelta(days=1)
        self.order.status_updated_at = previous_update

        with patch.object(Order, "save", lambda self, *args, **kwargs: None):
            Order.save(self.order)

        self.assertEqual(self.order.status_updated_at, previous_update)

    def test_clean_valid_email(self):
        result = Order.clean(self.order)

        self.assertIsNone(result)

    @patch("order.models.order.validate_email")
    def test_clean_invalid_email(self, mock_validate_email):
        mock_validate_email.side_effect = ValidationError("Invalid email")

        with self.assertRaises(ValidationError):
            Order.clean(self.order)

    def test_total_price_items_property(self):
        expected_total = Money("130.00", "USD")

        self.order.total_price_items = expected_total

        self.assertEqual(self.order.total_price_items, expected_total)

    def test_total_price_extra_property(self):
        expected_total = Money("10.00", "USD")

        with patch("order.models.order.settings.DEFAULT_CURRENCY", "USD"):
            result = Order.total_price_extra.__get__(self.order)

        self.assertEqual(result, expected_total)

    def test_full_address_property(self):
        expected_address = f"{self.order.street} {self.order.street_number}, {self.order.zipcode} {self.order.city}"

        result = Order.full_address.__get__(self.order)

        self.assertEqual(result, expected_address)

    def test_customer_full_name_property(self):
        expected_name = f"{self.order.first_name} {self.order.last_name}"

        result = Order.customer_full_name.__get__(self.order)

        self.assertEqual(result, expected_name)

    def test_is_paid_property_true(self):
        self.order.payment_status = PaymentStatusEnum.COMPLETED

        result = Order.is_paid.__get__(self.order)

        self.assertTrue(result)

    def test_is_paid_property_false(self):
        self.order.status = OrderStatusEnum.PENDING

        result = Order.is_paid.__get__(self.order)

        self.assertFalse(result)

    def test_can_be_canceled_property_true(self):
        self.order.status = OrderStatusEnum.PENDING

        result = Order.can_be_canceled.__get__(self.order)

        self.assertTrue(result)

    def test_can_be_canceled_property_false(self):
        self.order.status = OrderStatusEnum.SHIPPED

        result = Order.can_be_canceled.__get__(self.order)

        self.assertFalse(result)

    def test_is_completed_property_true(self):
        self.order.status = OrderStatusEnum.COMPLETED

        result = Order.is_completed.__get__(self.order)

        self.assertTrue(result)

    def test_is_completed_property_false(self):
        self.order.status = OrderStatusEnum.PENDING

        result = Order.is_completed.__get__(self.order)

        self.assertFalse(result)

    def test_is_canceled_property_true(self):
        self.order.status = OrderStatusEnum.CANCELED

        result = Order.is_canceled.__get__(self.order)

        self.assertTrue(result)

    def test_is_canceled_property_false(self):
        self.order.status = OrderStatusEnum.PENDING

        result = Order.is_canceled.__get__(self.order)

        self.assertFalse(result)

    def test_calculate_order_total_amount(self):
        items_total = Money("130.00", "USD")
        shipping_total = Money("10.00", "USD")
        expected_total = Money("140.00", "USD")

        with patch.object(
            Order, "total_price", new_callable=PropertyMock
        ) as mock_total_price:
            mock_total_price.__get__ = Mock(return_value=expected_total)

            self.order.total_price_items = items_total
            self.order.total_price_extra = shipping_total

            self.order.total_price = expected_total

            result = Order.calculate_order_total_amount(self.order)

            self.assertEqual(result, expected_total)

    @patch("django.utils.timezone.now")
    def test_mark_as_paid(self, mock_timezone_now):
        mock_now = timezone.now()
        mock_timezone_now.return_value = mock_now

        payment_id = "PAY123"
        payment_method = "credit_card"

        original_mark_as_paid = Order.mark_as_paid

        def patched_mark_as_paid(order, **kwargs):
            original_mark_as_paid(order, **kwargs)
            order.status_updated_at = mock_now

        with (
            patch.object(Order, "mark_as_paid", patched_mark_as_paid),
            patch.object(Order, "save"),
        ):
            Order.mark_as_paid(
                self.order,
                payment_id=payment_id,
                payment_method=payment_method,
            )

        self.assertEqual(self.order.payment_status, PaymentStatusEnum.COMPLETED)
        self.assertEqual(self.order.payment_id, payment_id)
        self.assertEqual(self.order.payment_method, payment_method)
        self.assertEqual(self.order.status_updated_at, mock_now)

    def test_add_tracking_info(self):
        tracking_number = "TRACK123"
        shipping_carrier = "FedEx"

        Order.add_tracking_info(self.order, tracking_number, shipping_carrier)

        self.assertEqual(self.order.tracking_number, tracking_number)
        self.assertEqual(self.order.shipping_carrier, shipping_carrier)


class OrderQuerySetTestCase(TestCase):
    def setUp(self):
        self.queryset = Mock(spec=OrderQuerySet)

        self.queryset.filter.return_value = self.queryset

        self.queryset.annotate.return_value = self.queryset

    def test_pending_filter(self):
        result = OrderQuerySet.pending(self.queryset)

        self.queryset.filter.assert_called_once_with(
            status=OrderStatusEnum.PENDING
        )
        self.assertEqual(result, self.queryset)

    def test_processing_filter(self):
        result = OrderQuerySet.processing(self.queryset)

        self.queryset.filter.assert_called_once_with(
            status=OrderStatusEnum.PROCESSING
        )
        self.assertEqual(result, self.queryset)

    def test_shipped_filter(self):
        result = OrderQuerySet.shipped(self.queryset)

        self.queryset.filter.assert_called_once_with(
            status=OrderStatusEnum.SHIPPED
        )
        self.assertEqual(result, self.queryset)

    def test_delivered_filter(self):
        result = OrderQuerySet.delivered(self.queryset)

        self.queryset.filter.assert_called_once_with(
            status=OrderStatusEnum.DELIVERED
        )
        self.assertEqual(result, self.queryset)

    def test_completed_filter(self):
        result = OrderQuerySet.completed(self.queryset)

        self.queryset.filter.assert_called_once_with(
            status=OrderStatusEnum.COMPLETED
        )
        self.assertEqual(result, self.queryset)

    def test_canceled_filter(self):
        result = OrderQuerySet.canceled(self.queryset)

        self.queryset.filter.assert_called_once_with(
            status=OrderStatusEnum.CANCELED
        )
        self.assertEqual(result, self.queryset)

    def test_returned_filter(self):
        result = OrderQuerySet.returned(self.queryset)

        self.queryset.filter.assert_called_once_with(
            status=OrderStatusEnum.RETURNED
        )
        self.assertEqual(result, self.queryset)

    def test_refunded_filter(self):
        result = OrderQuerySet.refunded(self.queryset)

        self.queryset.filter.assert_called_once_with(
            status=OrderStatusEnum.REFUNDED
        )
        self.assertEqual(result, self.queryset)

    @patch("django.db.models.Sum")
    @patch("django.db.models.ExpressionWrapper")
    @patch("django.db.models.F")
    def test_with_total_amounts(
        self, mock_f, mock_expression_wrapper, mock_sum
    ):
        mock_f.return_value = "price_expression"
        mock_expression_wrapper.return_value = "wrapper_expression"
        mock_sum.return_value = "sum_expression"

        result = OrderQuerySet.with_total_amounts(self.queryset)

        self.queryset.annotate.assert_called_once()
        self.assertEqual(result, self.queryset)


class OrderManagerTestCase(TestCase):
    def setUp(self):
        self.manager = Mock(spec=OrderManager)

        self.queryset = Mock(spec=OrderQuerySet)
        self.manager.get_queryset.return_value = self.queryset

        self.queryset.pending.return_value = "pending_result"
        self.queryset.processing.return_value = "processing_result"
        self.queryset.shipped.return_value = "shipped_result"
        self.queryset.delivered.return_value = "delivered_result"
        self.queryset.completed.return_value = "completed_result"
        self.queryset.canceled.return_value = "canceled_result"
        self.queryset.returned.return_value = "returned_result"
        self.queryset.refunded.return_value = "refunded_result"
        self.queryset.with_total_amounts.return_value = "total_amounts_result"

    def test_with_total_amounts(self):
        result = OrderManager.with_total_amounts(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.with_total_amounts.assert_called_once()
        self.assertEqual(result, "total_amounts_result")

    def test_pending(self):
        result = OrderManager.pending(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.pending.assert_called_once()
        self.assertEqual(result, "pending_result")

    def test_processing(self):
        result = OrderManager.processing(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.processing.assert_called_once()
        self.assertEqual(result, "processing_result")

    def test_shipped(self):
        result = OrderManager.shipped(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.shipped.assert_called_once()
        self.assertEqual(result, "shipped_result")

    def test_delivered(self):
        result = OrderManager.delivered(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.delivered.assert_called_once()
        self.assertEqual(result, "delivered_result")

    def test_completed(self):
        result = OrderManager.completed(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.completed.assert_called_once()
        self.assertEqual(result, "completed_result")

    def test_canceled(self):
        result = OrderManager.canceled(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.canceled.assert_called_once()
        self.assertEqual(result, "canceled_result")

    def test_returned(self):
        result = OrderManager.returned(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.returned.assert_called_once()
        self.assertEqual(result, "returned_result")

    def test_refunded(self):
        result = OrderManager.refunded(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.refunded.assert_called_once()
        self.assertEqual(result, "refunded_result")
