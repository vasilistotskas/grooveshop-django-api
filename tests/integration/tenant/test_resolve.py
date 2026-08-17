from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

_TENANT_SETTINGS = {
    "TENANT_SCHEMA_NAME": "webside",
    "TENANT_PRIMARY_DOMAIN": "webside.gr",
    "TENANT_EXTRA_DOMAINS": ["www.webside.gr"],
    "SITE_NAME": "Webside",
    "LANGUAGE_CODE": "el",
    "DEFAULT_CURRENCY": "EUR",
    "BOXNOW_PARTNER_ID": "",
    "META_PIXEL_ID": "",
}


@override_settings(**_TENANT_SETTINGS)
class TenantResolveViewTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_missing_domain_returns_400(self):
        response = self.client.get(reverse("tenant-resolve"))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "domain query parameter is required.",
        )

    def test_unknown_domain_returns_404(self):
        response = self.client.get(
            reverse("tenant-resolve"), {"domain": "not-a-store.example"}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["detail"], "Store not found.")

    def test_primary_domain_returns_camel_case_config_on_the_wire(self):
        """``response.data`` is the pre-render dict (still snake_case);
        the camelCase conversion only happens in the JSON renderer, so
        assert on the actual rendered bytes to prove the wire contract.
        """
        response = self.client.get(
            reverse("tenant-resolve"), {"domain": "webside.gr"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["schemaName"], "webside")
        self.assertEqual(body["name"], "Webside")
        self.assertEqual(body["storeName"], "Webside")
        self.assertEqual(body["defaultLocale"], "el")
        self.assertEqual(body["defaultCurrency"], "EUR")
        self.assertEqual(body["primaryDomain"], "webside.gr")
        self.assertTrue(body["loyaltyEnabled"])
        self.assertTrue(body["blogEnabled"])

    def test_extra_domain_resolves_to_the_same_tenant(self):
        response = self.client.get(
            reverse("tenant-resolve"), {"domain": "www.webside.gr"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["primaryDomain"], "webside.gr")

    @override_settings(
        TENANT_CHAT_API_KEY="tenant-chat-key",
        AGENT_GATEWAY_INTERNAL_SECRET="gw-secret",
    )
    def test_chat_api_key_requires_the_internal_secret(self):
        # Public callers never see the secret — with or without a bogus
        # token — and the cached payload stays public even after an
        # authenticated request populated it.
        public = self.client.get(
            reverse("tenant-resolve"), {"domain": "webside.gr"}
        )
        self.assertNotIn("chatApiKey", public.json())

        forged = self.client.get(
            reverse("tenant-resolve"),
            {"domain": "webside.gr"},
            HTTP_X_INTERNAL_TOKEN="wrong",
        )
        self.assertNotIn("chatApiKey", forged.json())

        gateway = self.client.get(
            reverse("tenant-resolve"),
            {"domain": "webside.gr"},
            HTTP_X_INTERNAL_TOKEN="gw-secret",
        )
        self.assertEqual(gateway.json()["chatApiKey"], "tenant-chat-key")

        after = self.client.get(
            reverse("tenant-resolve"), {"domain": "webside.gr"}
        )
        self.assertNotIn("chatApiKey", after.json())
        self.assertNotIn("chat_api_key", cache.get("tenant_resolve:webside.gr"))

    @override_settings(TENANT_CHAT_API_KEY="tenant-chat-key")
    def test_chat_api_key_withheld_when_no_internal_secret_configured(self):
        # An empty AGENT_GATEWAY_INTERNAL_SECRET must never match an
        # empty header — the secret stays withheld entirely.
        with override_settings(AGENT_GATEWAY_INTERNAL_SECRET=""):
            response = self.client.get(
                reverse("tenant-resolve"),
                {"domain": "webside.gr"},
                HTTP_X_INTERNAL_TOKEN="",
            )
        self.assertNotIn("chatApiKey", response.json())

    def test_successful_resolve_is_cached(self):
        first = self.client.get(
            reverse("tenant-resolve"), {"domain": "webside.gr"}
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        cached = cache.get("tenant_resolve:webside.gr")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["schema_name"], "webside")

        with mock.patch("tenant.views.get_tenant_config") as mocked_get_config:
            second = self.client.get(
                reverse("tenant-resolve"), {"domain": "webside.gr"}
            )

        mocked_get_config.assert_not_called()
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data, first.data)
