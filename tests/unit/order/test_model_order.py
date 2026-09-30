from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from djmoney.money import Money

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.managers.order import OrderQuerySet
from order.models.order import Order
from pay_way.enum.settlement import PaySettlement


class OrderModelTestCase(TestCase):
    def setUp(self):
        self.order = Mock(spec=Order)
        self.order.id = 1
        self.order.uuid = "test-uuid-1234"
        self.order.email = "customer@example.com"
        self.order.first_name = "John"
        self.order.last_name = "Doe"
        self.order.status = OrderStatus.PENDING
        self.order.created_at = timezone.now()
        self.order.document_type = OrderDocumentTypeEnum.RECEIPT
        self.order.shipping_price = Money("10.00", settings.DEFAULT_CURRENCY)
        self.order.paid_amount = Money("0.00", settings.DEFAULT_CURRENCY)
        self.order.payment_method_fee = None  # No payment method fee
        self.order._original_status = OrderStatus.PENDING

        self.order.street = "Test Street"
        self.order.street_number = "123"
        self.order.city = "Test City"
        self.order.zipcode = "12345"
        self.order.place = ""
        # Unsaved (no stored row), so ``clean()`` judges the address
        # against the country's postcode format.
        self.order.pk = None
        self.order.country_id = "GR"
        self.order.country = Mock(
            postal_code_pattern=r"\d{3} ?\d{2}", postal_code_example="151 24"
        )
        # ``regions.exists()`` on a bare Mock is itself a truthy Mock,
        # which the new "region required when the country has any"
        # rule reads as "yes, GR has regions" — pin it to the real
        # answer this test wants: no region required.
        self.order.country.regions.exists.return_value = False
        # No region on this order — a bare ``Mock()`` auto-attribute
        # would otherwise fail the "region belongs to country" check
        # (its ``country_id`` is itself a Mock, never equal to "GR").
        self.order.region = None
        # ``clean()`` also runs the payment-consistency helper; bind the
        # real one so a spec'd Mock does not answer it with a Mock.
        self.order.payment_status = PaymentStatus.PENDING
        self.order._payment_consistency_errors = lambda: (
            Order._payment_consistency_errors(self.order)
        )

        item1 = Mock()
        item1.product = Mock()
        item1.product.name = "Test Product 1"
        item1.quantity = 2
        item1.price = Money("50.00", settings.DEFAULT_CURRENCY)
        item1.total = Money("100.00", settings.DEFAULT_CURRENCY)

        item2 = Mock()
        item2.product = Mock()
        item2.product.name = "Test Product 2"
        item2.quantity = 1
        item2.price = Money("30.00", settings.DEFAULT_CURRENCY)
        item2.total = Money("30.00", settings.DEFAULT_CURRENCY)

        self.order.items = MagicMock()
        self.order.items.all.return_value = [item1, item2]

    def test_clean_valid_email(self):
        result = Order.clean(self.order)

        self.assertIsNone(result)

    @patch("order.models.order.validate_email")
    def test_clean_invalid_email(self, mock_validate_email):
        mock_validate_email.side_effect = ValidationError("Invalid email")

        with self.assertRaises(ValidationError):
            Order.clean(self.order)

    def test_customer_full_name_property(self):
        expected_name = f"{self.order.first_name} {self.order.last_name}"

        result = Order.customer_full_name.__get__(self.order)

        self.assertEqual(result, expected_name)

    def test_is_paid_property_true(self):
        self.order.payment_status = PaymentStatus.COMPLETED
        self.order.paid_amount = Money("100.00", settings.DEFAULT_CURRENCY)

        result = Order.is_paid.__get__(self.order)

        self.assertTrue(result)

    def test_is_paid_property_false(self):
        self.order.status = OrderStatus.PENDING

        result = Order.is_paid.__get__(self.order)

        self.assertFalse(result)

    def test_is_paid_when_deductions_covered_the_whole_total(self):
        """A gift card (or a 100% promotion, or a full loyalty
        redemption) settles the order without a provider, so the
        REMAINDER the customer owed is 0.00. Requiring a positive
        paid_amount made such an order report itself unpaid forever."""
        self.order.payment_status = PaymentStatus.COMPLETED
        self.order.paid_amount = Money("0.00", settings.DEFAULT_CURRENCY)
        self.order.payment_id = "GIFTCARD_abc"

        self.assertTrue(Order.is_paid.__get__(self.order))

    def test_fully_covered_order_no_longer_awaits_online_payment(self):
        """awaits_online_payment is `not is_paid` behind an online pay
        way, so the same gap kept a settled order in the "pay now" state
        and let the checkout endpoints open a zero-amount session."""
        self.order.payment_status = PaymentStatus.COMPLETED
        self.order.paid_amount = Money("0.00", settings.DEFAULT_CURRENCY)
        # ``settlement``, not the deprecated mirror: a bare Mock
        # attribute is not a valid PaySettlement and the property
        # now raises on one.
        self.order.pay_way = Mock(settlement=PaySettlement.ONLINE.value)
        # ``self.order`` is a Mock(spec=Order), which would hand back a
        # truthy stub for ``is_paid`` and make this assertion vacuous.
        # Feed it what the real property answers.
        self.order.is_paid = Order.is_paid.__get__(self.order)

        self.assertFalse(Order.awaits_online_payment.__get__(self.order))

    def test_is_paid_false_while_payment_is_pending(self):
        self.order.payment_status = PaymentStatus.PENDING
        self.order.paid_amount = Money("100.00", settings.DEFAULT_CURRENCY)

        self.assertFalse(Order.is_paid.__get__(self.order))

    def test_can_be_canceled_property_true(self):
        self.order.status = OrderStatus.PENDING

        result = Order.can_be_canceled.__get__(self.order)

        self.assertTrue(result)

    def test_can_be_canceled_property_false(self):
        self.order.status = OrderStatus.SHIPPED

        result = Order.can_be_canceled.__get__(self.order)

        self.assertFalse(result)

    def test_is_completed_property_true(self):
        self.order.status = OrderStatus.COMPLETED

        result = Order.is_completed.__get__(self.order)

        self.assertTrue(result)

    def test_is_completed_property_false(self):
        self.order.status = OrderStatus.PENDING

        result = Order.is_completed.__get__(self.order)

        self.assertFalse(result)

    def test_is_canceled_property_true(self):
        self.order.status = OrderStatus.CANCELED

        result = Order.is_canceled.__get__(self.order)

        self.assertTrue(result)

    def test_is_canceled_property_false(self):
        self.order.status = OrderStatus.PENDING

        result = Order.is_canceled.__get__(self.order)

        self.assertFalse(result)

    def test_add_tracking_info(self):
        tracking_number = "TRACK123"
        shipping_carrier = "FedEx"

        Order.add_tracking_info(self.order, tracking_number, shipping_carrier)

        self.assertEqual(self.order.tracking_number, tracking_number)
        self.assertEqual(self.order.shipping_carrier, shipping_carrier)

        # The pending method should be accessible via __getattr__
        # In a real scenario, it would delegate to the queryset


@pytest.mark.parametrize(
    ("method", "status"),
    [
        ("pending", OrderStatus.PENDING),
        ("processing", OrderStatus.PROCESSING),
        ("shipped", OrderStatus.SHIPPED),
        ("delivered", OrderStatus.DELIVERED),
        ("completed", OrderStatus.COMPLETED),
        ("canceled", OrderStatus.CANCELED),
        ("returned", OrderStatus.RETURNED),
        ("refunded", OrderStatus.REFUNDED),
    ],
)
def test_each_status_shortcut_filters_on_its_status(method, status):
    queryset = Mock(spec=OrderQuerySet)

    getattr(OrderQuerySet, method)(queryset)

    queryset.filter.assert_called_once_with(status=status)
