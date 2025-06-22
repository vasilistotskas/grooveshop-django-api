from django.contrib.auth import get_user_model
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.testing import TestURLFixerMixin
from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.serializers.item import (
    OrderItemDetailSerializer,
    OrderItemSerializer,
)
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


class OrderItemViewSetTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory()
        cls.admin_user = UserAccountFactory(is_staff=True, is_superuser=True)
        cls.other_user = UserAccountFactory()

        cls.pay_way = PayWayFactory()

        cls.order = OrderFactory(user=cls.user, pay_way=cls.pay_way)

        cls.other_order = OrderFactory(user=cls.other_user, pay_way=cls.pay_way)

        cls.product = ProductFactory(
            active=True, num_images=0, num_reviews=0, stock=20
        )
        cls.other_product = ProductFactory(
            active=True, num_images=0, num_reviews=0, stock=15
        )

        cls.order_item = cls.order.items.create(
            product=cls.product,
            price=Money("50.00", "EUR"),
            quantity=2,
        )

        cls.other_order_item = cls.other_order.items.create(
            product=cls.other_product,
            price=Money("30.00", "EUR"),
            quantity=1,
        )

    def get_order_item_detail_url(self, pk):
        return reverse("order-item-detail", kwargs={"pk": pk})

    def get_order_item_list_url(self):
        return reverse("order-item-list")

    def test_list(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.get_order_item_list_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        self.assertGreaterEqual(len(response.data["results"]), 1)

        user_order_ids = [order.id for order in [self.order]]
        for result in response.data["results"]:
            self.assertIn(result["order"], user_order_ids)

    def test_list_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.get_order_item_list_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        first_result = response.data["results"][0]
        expected_fields = set(OrderItemSerializer.Meta.fields)
        actual_fields = set(first_result.keys())
        self.assertEqual(actual_fields, expected_fields)

    def test_retrieve_valid(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            self.get_order_item_detail_url(self.order_item.id)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.order_item.id)
        self.assertEqual(response.data["quantity"], 2)

    def test_retrieve_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            self.get_order_item_detail_url(self.order_item.id)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_fields = set(OrderItemDetailSerializer.Meta.fields)
        actual_fields = set(response.data.keys())
        self.assertEqual(actual_fields, expected_fields)

    def test_retrieve_invalid(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.get_order_item_detail_url(99999))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_valid(self):
        self.client.force_authenticate(user=self.admin_user)

        payload = {
            "order": self.order.id,
            "product": self.other_product.id,
            "quantity": 3,
            "notes": "Test item creation",
        }

        response = self.client.post(
            self.get_order_item_list_url(), payload, format="json"
        )

        self.assertIn(
            response.status_code,
            [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST],
        )

    def test_create_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.admin_user)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": 1,
            "notes": "Test serializer validation",
        }

        response = self.client.post(
            self.get_order_item_list_url(), payload, format="json"
        )

        self.assertIn(
            response.status_code,
            [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST],
        )

    def test_update_valid(self):
        self.client.force_authenticate(user=self.user)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": 4,
            "notes": "Updated notes",
        }

        response = self.client.put(
            self.get_order_item_detail_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN],
        )

        if response.status_code == status.HTTP_200_OK:
            self.order_item.refresh_from_db()
            self.assertEqual(self.order_item.quantity, 4)
            self.assertEqual(self.order_item.notes, "Updated notes")

    def test_partial_update_valid(self):
        self.client.force_authenticate(user=self.user)

        payload = {"notes": "Partially updated notes"}

        response = self.client.patch(
            self.get_order_item_detail_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN],
        )

    def test_delete_valid(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.delete(
            self.get_order_item_detail_url(self.order_item.id)
        )

        self.assertIn(
            response.status_code,
            [status.HTTP_204_NO_CONTENT, status.HTTP_403_FORBIDDEN],
        )

    def test_user_can_only_access_own_order_items(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_detail_url(self.other_order_item.id)
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_can_access_all_order_items(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(
            self.get_order_item_detail_url(self.order_item.id)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(
            self.get_order_item_detail_url(self.other_order_item.id)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unauthenticated_access_denied(self):
        response = self.client.get(self.get_order_item_list_url())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_filter_by_order(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"order": self.order.id}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for result in response.data["results"]:
            self.assertEqual(result["order"], self.order.id)

    def test_filter_by_product(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"product": self.product.id}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["product"], self.product.id
        )

    def test_filter_by_quantity_range(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"quantity_min": 2}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for result in response.data["results"]:
            self.assertGreaterEqual(result["quantity"], 2)

    def test_filter_by_refund_status(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"is_refunded": "false"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for result in response.data["results"]:
            self.assertFalse(result["is_refunded"])

    def test_search_functionality(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"search": "test"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

    def test_ordering_functionality(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"ordering": "created_at"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

    def test_refund_action_valid(self):
        self.client.force_authenticate(user=self.user)

        self.order.status = OrderStatus.DELIVERED.value
        self.order.save()

        refund_url = reverse(
            "order-item-refund", kwargs={"pk": self.order_item.id}
        )
        payload = {"quantity": 1, "reason": "Product defective"}

        response = self.client.post(refund_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail", response.data)
        self.assertIn("refunded_amount", response.data)

    def test_refund_action_invalid_order_status(self):
        self.client.force_authenticate(user=self.user)

        self.order.status = OrderStatus.PENDING.value
        self.order.save()

        refund_url = reverse(
            "order-item-refund", kwargs={"pk": self.order_item.id}
        )
        payload = {"quantity": 1, "reason": "Test refund"}

        response = self.client.post(refund_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_refund_action_excessive_quantity(self):
        self.client.force_authenticate(user=self.user)

        self.order.status = OrderStatus.DELIVERED.value
        self.order.save()

        refund_url = reverse(
            "order-item-refund", kwargs={"pk": self.order_item.id}
        )
        payload = {
            "quantity": 10,
            "reason": "Test excessive refund",
        }

        response = self.client.post(refund_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validation_errors(self):
        self.client.force_authenticate(user=self.admin_user)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": -1,
        }

        response = self.client.post(
            self.get_order_item_list_url(), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stock_validation(self):
        self.client.force_authenticate(user=self.admin_user)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": 1000,
        }

        response = self.client.post(
            self.get_order_item_list_url(), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
