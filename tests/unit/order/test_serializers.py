from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from djmoney.money import Money

from country.factories import CountryFactory
from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.serializers.item import OrderItemDetailSerializer
from order.serializers.order import (
    OrderDetailSerializer,
    OrderWriteSerializer,
)
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory

User = get_user_model()


class OrderDetailSerializerTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test2@example.com",
            password="testpassword",
        )

        self.pay_way = PayWayFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)

        self.order = OrderFactory(
            user=self.user,
            pay_way=self.pay_way,
            country=self.country,
            region=self.region,
            status=OrderStatus.PENDING.value,
        )

        product = ProductFactory(
            name="Test Product", price=Money("50.00", settings.DEFAULT_CURRENCY)
        )
        self.order_item = self.order.items.create(
            product=product,
            price=Money("50.00", settings.DEFAULT_CURRENCY),
            quantity=2,
        )

        self.serializer = OrderDetailSerializer(instance=self.order)

    def test_contains_expected_fields(self):
        data = self.serializer.data

        expected_additional_fields = {
            "order_timeline",
            "pricing_breakdown",
            "tracking_details",
        }

        self.assertTrue(
            all(field in data for field in expected_additional_fields)
        )

    def test_timeline_is_curated_for_customer(self):
        """The customer-facing timeline is a curated subset of
        ``OrderHistory`` — only state transitions that matter to
        the buyer (STATUS, PAYMENT, SHIPPING, REFUND) plus the
        synthetic CREATED entry. Operational NOTE rows (item-added,
        order-created sentinel, confirmation-email-sent) belong
        in the admin audit log only; surfacing them duplicates
        information already visible on the order page and leaks
        internal recipient addresses.

        Lock the behaviour: NOTE is filtered out; STATUS/PAYMENT
        are rendered with from→to transitions; the synthetic
        CREATED entry carries no description (frontend renders
        the localised title on its own).
        """
        from order.models.history import OrderHistory

        OrderHistory.log_note(
            order=self.order,
            note="Shipping confirmation email sent to ops@example.com",
        )
        OrderHistory.log_status_change(
            order=self.order,
            previous_status="PENDING",
            new_status="PROCESSING",
        )
        OrderHistory.log_payment_update(
            order=self.order,
            previous_value={"payment_status": "PENDING"},
            new_value={
                "payment_status": "COMPLETED",
                "provider": "viva_wallet",
                "payment_id": "internal-token-do-not-expose",
            },
        )

        serializer = OrderDetailSerializer(instance=self.order)
        timeline = serializer.data["order_timeline"]
        change_types = [entry["change_type"] for entry in timeline]
        descriptions = [entry["description"] for entry in timeline]

        self.assertNotIn(
            "NOTE",
            change_types,
            "NOTE rows must be filtered from the customer timeline",
        )
        self.assertNotIn(
            "Shipping confirmation email sent to ops@example.com",
            descriptions,
            "NOTE note text must not leak into the customer timeline",
        )

        created = next(e for e in timeline if e["change_type"] == "CREATED")
        self.assertEqual(
            created["description"],
            "",
            "Synthetic CREATED entry must have empty description "
            "(frontend renders the localised title alone)",
        )

        self.assertIn("STATUS", change_types)
        self.assertIn("PENDING → PROCESSING", descriptions)

        payment_descs = [
            entry["description"]
            for entry in timeline
            if entry["change_type"] == "PAYMENT"
        ]
        self.assertTrue(payment_descs, "PAYMENT entry missing")
        payment_desc = payment_descs[-1]
        self.assertIn("PENDING → COMPLETED", payment_desc)
        self.assertIn("viva_wallet", payment_desc)
        self.assertNotIn(
            "internal-token-do-not-expose",
            payment_desc,
            "PAYMENT description must not surface the payment_id token",
        )


class OrderCreateUpdateSerializerTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test3@example.com",
            password="testpassword",
        )

        self.pay_way = PayWayFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)

        self.product = ProductFactory(
            stock=10, price=Money("50.00", settings.DEFAULT_CURRENCY)
        )

        self.valid_data = {
            "email": "customer@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "phone": "+12025550195",
            "paid_amount": {
                "amount": "100.00",
                "currency": settings.DEFAULT_CURRENCY,
            },
            "status": OrderStatus.PENDING.value,
            "shipping_price": {
                "amount": "10.00",
                "currency": settings.DEFAULT_CURRENCY,
            },
            "street": "Main Street",
            "street_number": "123",
            "city": "Testville",
            "zipcode": "12345",
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "pay_way": self.pay_way.id,
            "items": [
                {
                    "product": self.product.id,
                    "quantity": 2,
                    "price": {
                        "amount": "50.00",
                        "currency": settings.DEFAULT_CURRENCY,
                    },
                }
            ],
        }

    def test_items_validation(self):
        invalid_data = self.valid_data.copy()
        invalid_data["items"] = []

        serializer = OrderWriteSerializer(data=invalid_data)
        self.assertFalse(serializer.is_valid())

        self.assertFalse(serializer.is_valid())

    def test_paid_amount_is_read_only(self):
        data_with_paid = self.valid_data.copy()
        data_with_paid["paid_amount"] = {
            "amount": "999.99",
            "currency": settings.DEFAULT_CURRENCY,
        }

        serializer = OrderWriteSerializer(data=data_with_paid)
        # paid_amount is read_only, so it should be ignored (not cause error)
        # and not appear in validated_data if serializer is valid
        if serializer.is_valid():
            self.assertNotIn("paid_amount", serializer.validated_data)

    def test_phone_number_validation(self):
        invalid_data = self.valid_data.copy()
        invalid_data["phone"] = "not-a-phone-number"

        serializer = OrderWriteSerializer(data=invalid_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("phone", serializer.errors)

    def test_required_fields(self):
        invalid_data = self.valid_data.copy()
        del invalid_data["email"]

        serializer = OrderWriteSerializer(data=invalid_data)
        serializer.is_valid()
        self.assertIn("email", serializer.errors)

        invalid_data = self.valid_data.copy()
        del invalid_data["first_name"]

        serializer = OrderWriteSerializer(data=invalid_data)
        serializer.is_valid()
        self.assertIn("first_name", serializer.errors)

    def test_email_validation(self):
        invalid_data = self.valid_data.copy()
        invalid_data["email"] = "not-an-email"

        serializer = OrderWriteSerializer(data=invalid_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("email", serializer.errors)


class OrderItemSerializerTestCase(TestCase):
    def setUp(self):
        self.product = ProductFactory(
            name="Test Product", price=Money("50.00", settings.DEFAULT_CURRENCY)
        )
        self.order = OrderFactory()
        self.order_item = self.order.items.create(
            product=self.product,
            price=Money("50.00", settings.DEFAULT_CURRENCY),
            quantity=2,
        )

        self.serializer = OrderItemDetailSerializer(instance=self.order_item)

    def test_contains_expected_fields(self):
        data = self.serializer.data

        expected_fields = {
            "id",
            "price",
            "product",
            "quantity",
            "original_quantity",
            "is_refunded",
            "refunded_quantity",
            "net_quantity",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "total_price",
            "refunded_amount",
            "net_price",
            "notes",
        }

        self.assertTrue(all(field in data for field in expected_fields))
