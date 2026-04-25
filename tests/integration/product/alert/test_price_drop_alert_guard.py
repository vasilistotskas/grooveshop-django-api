"""Integration tests for the price-drop alert feature flag.

Covers:
- POST /product/alert rejects with 403 when price_drop_alerts_enabled=False
- POST /product/alert succeeds when price_drop_alerts_enabled=True
- product-detail response includes price_drop_alerts_enabled field
- list response does NOT include price_drop_alerts_enabled field
"""

from __future__ import annotations

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.product import ProductFactory
from product.models.alert import ProductAlertKind
from user.factories.account import UserAccountFactory


class PriceDropAlertGuardTestCase(APITestCase):
    """Guard: new price-drop subscriptions blocked when flag is False."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory(num_addresses=0)
        cls.product_disabled = ProductFactory(
            price_drop_alerts_enabled=False,
            active=True,
        )
        cls.product_enabled = ProductFactory(
            price_drop_alerts_enabled=True,
            active=True,
        )
        cls.alert_list_url = reverse("product-alert-list")

    def _price_drop_payload(self, product):
        return {
            "kind": ProductAlertKind.PRICE_DROP,
            "product": product.pk,
            "targetPrice": str(product.price.amount - 1),
            "targetPriceCurrency": "EUR",
        }

    # ------------------------------------------------------------------
    # Flag OFF — expect 403
    # ------------------------------------------------------------------

    def test_price_drop_blocked_when_flag_false_authenticated(self):
        self.client.force_authenticate(user=self.user)
        payload = self._price_drop_payload(self.product_disabled)
        response = self.client.post(self.alert_list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("detail", response.data)

    def test_price_drop_blocked_when_flag_false_anonymous(self):
        self.client.force_authenticate(None)
        payload = self._price_drop_payload(self.product_disabled)
        payload["email"] = "guest@example.com"
        response = self.client.post(self.alert_list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ------------------------------------------------------------------
    # Flag ON — expect 201
    # ------------------------------------------------------------------

    def test_price_drop_allowed_when_flag_true_authenticated(self):
        self.client.force_authenticate(user=self.user)
        payload = self._price_drop_payload(self.product_enabled)
        response = self.client.post(self.alert_list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_price_drop_allowed_when_flag_true_anonymous(self):
        self.client.force_authenticate(None)
        payload = self._price_drop_payload(self.product_enabled)
        payload["email"] = "guest2@example.com"
        response = self.client.post(self.alert_list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Restock alerts unaffected by the flag
    # ------------------------------------------------------------------

    def test_restock_alert_unaffected_by_price_drop_flag(self):
        """Restock alert creation must not be blocked by the price_drop flag."""
        self.client.force_authenticate(user=self.user)
        payload = {
            "kind": ProductAlertKind.RESTOCK,
            "product": self.product_disabled.pk,
        }
        response = self.client.post(self.alert_list_url, payload, format="json")
        # 201 — the price_drop flag has no bearing on restock subscriptions
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class PriceDropAlertSerializerTestCase(APITestCase):
    """Field presence on detail vs list endpoints."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = UserAccountFactory(
            num_addresses=0, is_superuser=True, is_staff=True
        )
        cls.product = ProductFactory(
            price_drop_alerts_enabled=True,
            active=True,
        )

    def test_detail_includes_price_drop_alerts_enabled(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse("product-detail", args=[self.product.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # response.data holds DRF-parsed data (snake_case); camelCase
        # conversion only applies to the rendered JSON body.
        self.assertIn("price_drop_alerts_enabled", response.data)
        self.assertTrue(response.data["price_drop_alerts_enabled"])

    def test_list_does_not_include_price_drop_alerts_enabled(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse("product-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", [])
        if results:
            self.assertNotIn("price_drop_alerts_enabled", results[0])
