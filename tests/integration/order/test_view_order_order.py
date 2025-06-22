import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APITestCase

from core.enum import FloorChoicesEnum, LocationChoicesEnum
from core.utils.testing import TestURLFixerMixin
from country.factories import CountryFactory
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.serializers.order import (
    OrderDetailSerializer,
    OrderSerializer,
)
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


class OrderViewSetTestCase(TestURLFixerMixin, APITestCase):
    def setUp(self):
        super().setUp()

        self.user = UserAccountFactory()
        self.admin_user = UserAccountFactory(is_staff=True, is_superuser=True)
        self.other_user = UserAccountFactory()

        self.pay_way = PayWayFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)

        self.order = OrderFactory(
            user=self.user,
            status=OrderStatus.PENDING.value,
            pay_way=self.pay_way,
            country=self.country,
            region=self.region,
        )

        products = ProductFactory.create_batch(
            2, active=True, num_images=0, num_reviews=0, stock=20
        )
        self.order_items = []

        for i, product in enumerate(products):
            item = self.order.items.create(
                product=product,
                price=Money("50.00", settings.DEFAULT_CURRENCY),
                quantity=2 if i == 0 else 3,
            )
            self.order_items.append(item)

        self.other_order = OrderFactory(
            user=self.other_user,
            status=OrderStatus.SHIPPED.value,
            city="Chicago",
            first_name="Jane",
            last_name="Smith",
            tracking_number="TRACK123",
            payment_status=PaymentStatus.COMPLETED.value,
        )

        self.list_url = reverse("order-list")

    def get_order_detail_url(self, order_id):
        return reverse("order-detail", kwargs={"pk": order_id})

    def test_list_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        first_result = response.data["results"][0]
        expected_fields = set(OrderSerializer.Meta.fields)
        actual_fields = set(first_result.keys())
        self.assertEqual(actual_fields, expected_fields)

    def test_retrieve_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.get_order_detail_url(self.order.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_fields = set(OrderDetailSerializer.Meta.fields)
        actual_fields = set(response.data.keys())
        self.assertEqual(actual_fields, expected_fields)

    def test_create_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.admin_user)

        unique_email = f"test-{uuid.uuid4().hex[:8]}@example.com"

        payload = {
            "user": self.user.id,
            "pay_way": self.pay_way.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "floor": FloorChoicesEnum.FIRST_FLOOR.value,
            "location_type": LocationChoicesEnum.HOME.value,
            "email": unique_email,
            "first_name": "John",
            "last_name": "Doe",
            "street": "123 Main St",
            "street_number": "Apt 4",
            "city": "New York",
            "zipcode": "10001",
            "phone": "+12345678901",
            "shipping_price": Decimal("10.00"),
            "items": [
                {
                    "product": self.order_items[0].product.id,
                    "quantity": 1,
                }
            ],
        }

        response = self.client.post(self.list_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        expected_fields = set(OrderDetailSerializer.Meta.fields)
        actual_fields = set(response.data.keys())
        self.assertEqual(actual_fields, expected_fields)

    def test_update_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.admin_user)

        unique_email = f"update-serializer-{uuid.uuid4().hex[:8]}@example.com"

        payload = {
            "user": self.user.id,
            "pay_way": self.pay_way.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "email": unique_email,
            "first_name": "Updated",
            "last_name": "Name",
            "street": "456 New St",
            "street_number": "Unit 1",
            "city": "Boston",
            "zipcode": "02101",
            "phone": "+12345678902",
            "shipping_price": Decimal("15.00"),
            "items": [
                {
                    "product": self.order_items[0].product.id,
                    "quantity": 2,
                }
            ],
        }

        response = self.client.put(
            self.get_order_detail_url(self.order.id), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_fields = set(OrderDetailSerializer.Meta.fields)
        actual_fields = set(response.data.keys())
        self.assertEqual(actual_fields, expected_fields)

    def test_partial_update_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.admin_user)

        payload = {"city": "Updated City"}
        response = self.client.patch(
            self.get_order_detail_url(self.order.id), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_fields = set(OrderDetailSerializer.Meta.fields)
        actual_fields = set(response.data.keys())
        self.assertEqual(actual_fields, expected_fields)

    def test_list_orders(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)
        self.assertEqual(len(response.data["results"]), 2)

    def test_create_order(self):
        self.client.force_authenticate(user=self.admin_user)

        unique_email = f"create-{uuid.uuid4().hex[:8]}@example.com"

        initial_count = Order.objects.count()
        payload = {
            "user": self.user.id,
            "pay_way": self.pay_way.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "email": unique_email,
            "first_name": "New",
            "last_name": "Order",
            "street": "789 Test St",
            "street_number": "5",
            "city": "Seattle",
            "zipcode": "98101",
            "phone": "+12345678903",
            "shipping_price": Decimal("12.00"),
            "items": [
                {
                    "product": self.order_items[0].product.id,
                    "quantity": 1,
                }
            ],
        }

        response = self.client.post(self.list_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Order.objects.count(), initial_count + 1)

        created_order = Order.objects.get(email=unique_email)
        self.assertEqual(created_order.first_name, "New")
        self.assertEqual(created_order.city, "Seattle")

    def test_retrieve_order(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.get_order_detail_url(self.order.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.order.id)
        self.assertEqual(response.data["first_name"], self.order.first_name)

    def test_update_order(self):
        self.client.force_authenticate(user=self.admin_user)

        unique_email = f"updated-{uuid.uuid4().hex[:8]}@example.com"

        payload = {
            "user": self.user.id,
            "pay_way": self.pay_way.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "email": unique_email,
            "first_name": "Updated",
            "last_name": "Order",
            "street": "456 Updated St",
            "street_number": "10",
            "city": "Portland",
            "zipcode": "97201",
            "phone": "+12345678904",
            "shipping_price": Decimal("20.00"),
            "items": [
                {
                    "product": self.order_items[0].product.id,
                    "quantity": 3,
                }
            ],
        }

        response = self.client.put(
            self.get_order_detail_url(self.order.id), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.first_name, "Updated")
        self.assertEqual(self.order.city, "Portland")

    def test_delete_order(self):
        self.client.force_authenticate(user=self.admin_user)

        order_id = self.order.id
        response = self.client.delete(self.get_order_detail_url(order_id))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Order.objects.filter(id=order_id).exists())

    def test_retrieve_nonexistent_order(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.get_order_detail_url(99999))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_filter_by_status(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(
            self.list_url, {"status": OrderStatus.SHIPPED.value}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], OrderStatus.SHIPPED.value)

    def test_filter_by_user(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url, {"user": self.user.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user"], self.user.id)

    def test_filter_by_payment_status(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(
            self.list_url, {"payment_status": PaymentStatus.COMPLETED.value}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        for result in results:
            self.assertEqual(
                result["payment_status"], PaymentStatus.COMPLETED.value
            )

    def test_filter_by_city(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url, {"city": "Chicago"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertIn("Chicago", results[0]["city"])

    def test_filter_by_date_range(self):
        self.client.force_authenticate(user=self.admin_user)

        yesterday = self.order.created_at.replace(
            day=self.order.created_at.day - 1
        )
        response = self.client.get(
            self.list_url, {"created_after": yesterday.isoformat()}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)

    def test_filter_has_tracking(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url, {"has_tracking": "true"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        for result in results:
            detail_response = self.client.get(
                self.get_order_detail_url(result["id"])
            )
            self.assertIsNotNone(detail_response.data.get("tracking_number"))
            self.assertNotEqual(detail_response.data.get("tracking_number"), "")

    def test_filter_has_user(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url, {"has_user": "true"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        for result in results:
            self.assertIsNotNone(result["user"])

    def test_ordering_by_created_at_desc(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url, {"ordering": "-created_at"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 2)

        first_date = results[0]["created_at"]
        second_date = results[1]["created_at"]
        self.assertGreaterEqual(first_date, second_date)

    def test_ordering_by_status(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url, {"ordering": "status"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 2)

    def test_ordering_by_paid_amount(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url, {"ordering": "paid_amount"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_by_customer_name(self):
        self.client.force_authenticate(user=self.admin_user)

        search_name = self.order.first_name
        response = self.client.get(self.list_url, {"search": search_name})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertGreater(len(results), 0)
        found_match = any(
            search_name.lower() in result["first_name"].lower()
            for result in results
        )
        self.assertTrue(found_match)

    def test_search_by_email(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url, {"search": self.order.email})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertGreater(len(results), 0)

    def test_search_by_tracking_number(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url, {"search": "TRACK123"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        detail_response = self.client.get(
            self.get_order_detail_url(results[0]["id"])
        )
        self.assertEqual(detail_response.data["tracking_number"], "TRACK123")

    def test_search_by_city(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url, {"search": "Chicago"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertIn("Chicago", results[0]["city"])

    def test_validation_errors(self):
        self.client.force_authenticate(user=self.admin_user)

        payload = {
            "email": "invalid-email",
            "phone": "invalid-phone",
            "shipping_price": "invalid-price",
            "items": [],
        }

        response = self.client.post(self.list_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_create_order_with_invalid_items(self):
        self.client.force_authenticate(user=self.admin_user)

        payload = {
            "user": self.user.id,
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
            "street": "123 Test St",
            "street_number": "1",
            "city": "Test City",
            "zipcode": "12345",
            "phone": "+11234567890",
            "shipping_price": Decimal("10.00"),
            "items": [
                {
                    "product": 99999,
                    "quantity": 1,
                }
            ],
        }

        response = self.client.post(self.list_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_user_can_access_own_orders_only(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.get_order_detail_url(self.order.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(
            self.get_order_detail_url(self.other_order.id)
        )
        self.assertIn(
            response.status_code,
            [
                status.HTTP_200_OK,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            ],
        )

    def test_admin_can_access_all_orders(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(self.get_order_detail_url(self.order.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(
            self.get_order_detail_url(self.other_order.id)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unauthenticated_access_denied(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.get(self.get_order_detail_url(self.order.id))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_my_orders_action(self):
        self.client.force_authenticate(user=self.user)

        url = reverse("order-my-orders")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        for result in results:
            self.assertEqual(result["user"], self.user.id)

    def test_cancel_order_action(self):
        self.client.force_authenticate(user=self.admin_user)

        url = reverse("order-cancel", kwargs={"pk": self.order.id})
        response = self.client.post(url)

        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST],
        )

    def test_add_tracking_action_admin_only(self):
        self.client.force_authenticate(user=self.user)

        url = reverse("order-add-tracking", kwargs={"pk": self.order.id})
        payload = {
            "tracking_number": "TEST123",
            "shipping_carrier": "TestCarrier",
        }
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_status_action_admin_only(self):
        self.client.force_authenticate(user=self.user)

        url = reverse("order-update-status", kwargs={"pk": self.order.id})
        payload = {"status": OrderStatus.SHIPPED.value}
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
