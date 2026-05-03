"""C8 — Review create must require a completed purchase for the product."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.testing import TestURLFixerMixin
from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.factories.item import OrderItemFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


class ReviewVerifiedPurchaseTestCase(TestURLFixerMixin, APITestCase):
    """Only users with a COMPLETED order item for the product may leave a review."""

    @classmethod
    def setUpTestData(cls):
        cls.user_with_purchase = UserAccountFactory()
        cls.user_without_purchase = UserAccountFactory()
        cls.product = ProductFactory.create(stock=10, active=True)
        cls.product.set_current_language("en")
        cls.product.name = "Purchasable Product"
        cls.product.save()

        # Create a completed order containing the product for user_with_purchase
        cls.completed_order = OrderFactory.create(
            user=cls.user_with_purchase,
            status=OrderStatus.COMPLETED,
            num_order_items=0,
        )
        OrderItemFactory.create(
            order=cls.completed_order,
            product=cls.product,
            quantity=1,
        )

        cls.list_url = reverse("product-review-list")

    def _review_payload(self):
        return {
            "product": self.product.pk,
            "rate": 5,
            "translations": {
                "en": {"comment": "Great item"},
            },
        }

    def test_user_with_completed_purchase_can_create_review(self):
        self.client.force_authenticate(user=self.user_with_purchase)
        response = self.client.post(
            self.list_url, self._review_payload(), format="json"
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            response.data,
        )

    def test_user_without_purchase_cannot_create_review(self):
        self.client.force_authenticate(user=self.user_without_purchase)
        response = self.client.post(
            self.list_url, self._review_payload(), format="json"
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            response.data,
        )
        # Error key should be "product"
        error_detail = response.data
        self.assertIn("product", error_detail)

    def test_user_with_only_pending_order_cannot_create_review(self):
        user_pending = UserAccountFactory()
        pending_order = OrderFactory.create(
            user=user_pending,
            status=OrderStatus.PENDING,
            num_order_items=0,
        )
        OrderItemFactory.create(
            order=pending_order,
            product=self.product,
            quantity=1,
        )
        self.client.force_authenticate(user=user_pending)
        response = self.client.post(
            self.list_url, self._review_payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unauthenticated_user_gets_401(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            self.list_url, self._review_payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
