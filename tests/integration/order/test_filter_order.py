from django.contrib.auth import get_user_model
from django.urls import reverse
from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APITestCase
from djmoney.money import Money

from order.factories.order import OrderFactory
from order.models.order import Order
from order.enum.status import OrderStatus, PaymentStatus
from user.factories.account import UserAccountFactory
from country.factories import CountryFactory
from region.factories import RegionFactory
from pay_way.factories import PayWayFactory

User = get_user_model()


class OrderFilterTest(APITestCase):
    def setUp(self):
        Order.objects.all().delete()

        self.admin_user = UserAccountFactory(
            email="admin@example.com",
            first_name="Admin",
            last_name="User",
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.admin_user)

        self.user1 = UserAccountFactory(
            email="user1@example.com",
            first_name="John",
            last_name="Doe",
            is_active=True,
        )
        self.user2 = UserAccountFactory(
            email="user2@example.com",
            first_name="Jane",
            last_name="Smith",
            is_active=True,
        )
        self.inactive_user = UserAccountFactory(
            email="inactive@example.com",
            first_name="Inactive",
            last_name="User",
            is_active=False,
        )

        self.country1 = CountryFactory(alpha_2="US")
        self.country2 = CountryFactory(alpha_2="DE")
        self.region1 = RegionFactory(country=self.country1)
        self.region2 = RegionFactory(country=self.country2)

        self.pay_way1 = PayWayFactory(is_online_payment=True)
        self.pay_way2 = PayWayFactory(is_online_payment=False)

        self.now = timezone.now()

        self.pending_order = OrderFactory.build(
            user=self.user1,
            country=self.country1,
            region=self.region1,
            pay_way=self.pay_way1,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            first_name="John",
            last_name="Doe",
            email="john.doe@example.com",
            city="New York",
            zipcode="10001",
            street="Broadway",
            street_number="123",
            phone="+1234567890",
            mobile_phone="+1234567891",
            customer_notes="Please deliver after 5 PM",
            paid_amount=Money(0, "EUR"),
            shipping_price=Money(10, "EUR"),
            tracking_number="",
            payment_id="",
            status_updated_at=None,
        )
        self.pending_order.save()
        Order.objects.filter(id=self.pending_order.id).update(
            created_at=self.now - timedelta(hours=1)
        )

        self.processing_order = OrderFactory.build(
            user=self.user1,
            country=self.country1,
            region=self.region1,
            pay_way=self.pay_way1,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
            first_name="John",
            last_name="Doe",
            email="john.doe@example.com",
            city="New York",
            zipcode="10001",
            street="Broadway",
            street_number="456",
            phone="+1234567890",
            mobile_phone=None,
            customer_notes="",
            paid_amount=Money(50, "EUR"),
            shipping_price=Money(15, "EUR"),
            tracking_number="",
            payment_id="pay_123456",
            status_updated_at=self.now - timedelta(minutes=30),
        )
        self.processing_order.save()
        Order.objects.filter(id=self.processing_order.id).update(
            created_at=self.now - timedelta(hours=2)
        )

        self.shipped_order = OrderFactory.build(
            user=self.user2,
            country=self.country2,
            region=self.region2,
            pay_way=self.pay_way2,
            status=OrderStatus.SHIPPED,
            payment_status=PaymentStatus.COMPLETED,
            first_name="Jane",
            last_name="Smith",
            email="jane.smith@example.com",
            city="Berlin",
            zipcode="10115",
            street="Unter den Linden",
            street_number="1",
            phone="+49123456789",
            mobile_phone="+49123456788",
            customer_notes="Ring the bell twice",
            paid_amount=Money(75, "EUR"),
            shipping_price=Money(20, "EUR"),
            tracking_number="TRACK123456",
            payment_id="pay_789012",
            shipping_carrier="DHL",
            status_updated_at=self.now - timedelta(hours=6),
        )
        self.shipped_order.save()
        Order.objects.filter(id=self.shipped_order.id).update(
            created_at=self.now - timedelta(days=1)
        )

        self.completed_order = OrderFactory.build(
            user=self.user2,
            country=self.country2,
            region=self.region2,
            pay_way=self.pay_way1,
            status=OrderStatus.COMPLETED,
            payment_status=PaymentStatus.COMPLETED,
            first_name="Jane",
            last_name="Smith",
            email="jane.smith@example.com",
            city="Munich",
            zipcode="80331",
            street="Marienplatz",
            street_number="8",
            phone="+49123456789",
            mobile_phone="+49123456788",
            customer_notes="",
            paid_amount=Money(100, "EUR"),
            shipping_price=Money(5, "EUR"),
            tracking_number="TRACK789012",
            payment_id="pay_345678",
            shipping_carrier="FEDEX",
            status_updated_at=self.now - timedelta(days=2),
        )
        self.completed_order.save()
        Order.objects.filter(id=self.completed_order.id).update(
            created_at=self.now - timedelta(days=7)
        )

        self.canceled_order = OrderFactory.build(
            user=None,
            country=self.country1,
            region=self.region1,
            pay_way=self.pay_way2,
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.FAILED,
            first_name="Guest",
            last_name="User",
            email="guest@example.com",
            city="Los Angeles",
            zipcode="90210",
            street="Sunset Blvd",
            street_number="999",
            phone="+1987654321",
            mobile_phone=None,
            customer_notes="",
            paid_amount=Money(0, "EUR"),
            shipping_price=Money(12, "EUR"),
            tracking_number="",
            payment_id="",
            status_updated_at=self.now - timedelta(days=5),
        )
        self.canceled_order.save()
        Order.objects.filter(id=self.canceled_order.id).update(
            created_at=self.now - timedelta(days=35)
        )

    def test_basic_filters(self):
        url = reverse("order-list")

        response = self.client.get(url, {"user": self.user1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)

        response = self.client.get(url, {"status": OrderStatus.SHIPPED.value})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.shipped_order.id, result_ids)

        response = self.client.get(
            url, {"payment_status": PaymentStatus.COMPLETED.value}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.processing_order.id, result_ids)
        self.assertIn(self.shipped_order.id, result_ids)
        self.assertIn(self.completed_order.id, result_ids)

        response = self.client.get(url, {"country": self.country1.pk})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)
        self.assertIn(self.canceled_order.id, result_ids)

    def test_user_relationship_filters(self):
        url = reverse("order-list")

        response = self.client.get(url, {"user__email": "user1"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)

        response = self.client.get(url, {"user__first_name": "jane"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.shipped_order.id, result_ids)
        self.assertIn(self.completed_order.id, result_ids)

        response = self.client.get(url, {"user__is_active": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertNotIn(self.canceled_order.id, result_ids)

    def test_customer_information_filters(self):
        url = reverse("order-list")

        response = self.client.get(url, {"first_name": "john"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)

        response = self.client.get(url, {"email": "jane.smith"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.shipped_order.id, result_ids)
        self.assertIn(self.completed_order.id, result_ids)

        response = self.client.get(url, {"phone": "+1234567890"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)

    def test_address_filters(self):
        url = reverse("order-list")

        response = self.client.get(url, {"city": "new york"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)

        response = self.client.get(url, {"zipcode": "10115"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.shipped_order.id, result_ids)

        response = self.client.get(url, {"street": "broadway"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)

    def test_amount_filters(self):
        url = reverse("order-list")

        response = self.client.get(url, {"paid_amount_min": "50"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.processing_order.id, result_ids)
        self.assertIn(self.shipped_order.id, result_ids)
        self.assertIn(self.completed_order.id, result_ids)

        response = self.client.get(url, {"paid_amount_max": "75"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertNotIn(self.completed_order.id, result_ids)

        response = self.client.get(url, {"shipping_price_min": "15"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.processing_order.id, result_ids)
        self.assertIn(self.shipped_order.id, result_ids)

    def test_boolean_filters(self):
        url = reverse("order-list")

        response = self.client.get(url, {"has_user": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertNotIn(self.canceled_order.id, result_ids)

        response = self.client.get(url, {"has_user": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.canceled_order.id, result_ids)

        response = self.client.get(url, {"has_tracking": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.shipped_order.id, result_ids)
        self.assertIn(self.completed_order.id, result_ids)

        response = self.client.get(url, {"has_payment_id": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.processing_order.id, result_ids)
        self.assertIn(self.shipped_order.id, result_ids)
        self.assertIn(self.completed_order.id, result_ids)

        response = self.client.get(url, {"has_mobile_phone": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.processing_order.id, result_ids)
        self.assertIn(self.canceled_order.id, result_ids)

        response = self.client.get(url, {"has_customer_notes": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.shipped_order.id, result_ids)

        response = self.client.get(url, {"is_paid": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.processing_order.id, result_ids)
        self.assertIn(self.shipped_order.id, result_ids)
        self.assertIn(self.completed_order.id, result_ids)

    def test_special_filters(self):
        url = reverse("order-list")

        response = self.client.get(url, {"active_orders": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        active_statuses = OrderStatus.get_active_statuses()
        expected_count = sum(
            1
            for order in [
                self.pending_order,
                self.processing_order,
                self.shipped_order,
                self.completed_order,
                self.canceled_order,
            ]
            if order.status in active_statuses
        )
        self.assertEqual(len(result_ids), expected_count)

        response = self.client.get(url, {"final_orders": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        final_statuses = OrderStatus.get_final_statuses()
        expected_count = sum(
            1
            for order in [
                self.pending_order,
                self.processing_order,
                self.shipped_order,
                self.completed_order,
                self.canceled_order,
            ]
            if order.status in final_statuses
        )
        self.assertEqual(len(result_ids), expected_count)

        response = self.client.get(url, {"recent_orders": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertNotIn(self.canceled_order.id, result_ids)

        response = self.client.get(url, {"needs_processing": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)

        response = self.client.get(url, {"can_be_canceled": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)

    def test_timestamp_filters(self):
        url = reverse("order-list")

        created_after_date = self.now - timedelta(hours=3)
        response = self.client.get(
            url, {"created_after": created_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)

        status_updated_after_date = self.now - timedelta(hours=1)
        response = self.client.get(
            url, {"status_updated_after": status_updated_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.processing_order.id, result_ids)

        response = self.client.get(url, {"has_status_updated_at": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.pending_order.id, result_ids)

    def test_bulk_filters(self):
        url = reverse("order-list")

        user_ids = f"{self.user1.id},{self.user2.id}"
        response = self.client.get(url, {"user_ids": user_ids})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertNotIn(self.canceled_order.id, result_ids)

        status_list = (
            f"{OrderStatus.PENDING.value},{OrderStatus.COMPLETED.value}"
        )
        response = self.client.get(url, {"status_list": status_list})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.completed_order.id, result_ids)

        country_ids = f"{self.country1.pk}"
        response = self.client.get(url, {"country_ids": country_ids})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.pending_order.id, result_ids)
        self.assertIn(self.processing_order.id, result_ids)
        self.assertIn(self.canceled_order.id, result_ids)

        response = self.client.get(url, {"user_ids": "invalid,ids"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 0)

    def test_uuid_filter(self):
        url = reverse("order-list")

        response = self.client.get(url, {"uuid": str(self.pending_order.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.pending_order.id
        )

    def test_camel_case_filters(self):
        url = reverse("order-list")

        response = self.client.get(
            url,
            {
                "user__email": "user1",
                "payment_status": PaymentStatus.COMPLETED.value,
                "has_tracking": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.processing_order.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("order-list")

        response = self.client.get(
            url,
            {
                "user__is_active": "true",
                "is_paid": "true",
                "country": self.country1.pk,
                "ordering": "-created_at",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.processing_order.id, result_ids)

        created_after_date = self.now - timedelta(days=2)
        response = self.client.get(
            url,
            {
                "created_after": created_after_date.isoformat(),
                "has_tracking": "true",
                "payment_status": PaymentStatus.COMPLETED.value,
                "ordering": "-paid_amount",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.shipped_order.id, result_ids)

    def test_filter_with_ordering(self):
        url = reverse("order-list")

        response = self.client.get(
            url, {"is_paid": "true", "ordering": "-paid_amount"}
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual(len(results), 3)

        self.assertEqual(results[0]["id"], self.completed_order.id)
        self.assertEqual(results[1]["id"], self.shipped_order.id)
        self.assertEqual(results[2]["id"], self.processing_order.id)

        response = self.client.get(
            url, {"user": self.user1.id, "ordering": "-created_at"}
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual(len(results), 2)

        self.assertEqual(results[0]["id"], self.pending_order.id)
        self.assertEqual(results[1]["id"], self.processing_order.id)

    def tearDown(self):
        Order.objects.all().delete()
