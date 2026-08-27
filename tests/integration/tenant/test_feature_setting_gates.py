"""Merchant feature toggles (IsSettingEnabled) 404 their endpoints.

Each gated surface must be indistinguishable from a missing route when
the merchant turns its extra-setting off, and must work normally when
it is on (the default). The public settings themselves are covered by
test_settings_api.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _settings_off(*keys):
    disabled = set(keys)

    def _get(key, default=None):
        if key in disabled:
            return False
        return default

    return patch("extra_settings.models.Setting.get", side_effect=_get)


GATED_LIST_ENDPOINTS = [
    ("PRODUCT_REVIEWS_ENABLED", "product-review-list", False),
    ("FAVOURITES_ENABLED", "product-favourite-list", True),
    ("BLOG_COMMENTS_ENABLED", "blog-comment-list", False),
    ("NEWSLETTER_ENABLED", "user-subscription-topic-list", True),
    ("NEWSLETTER_ENABLED", "user-subscription-list", True),
    ("PRODUCT_ALERTS_ENABLED", "product-alert-list", True),
]


class TestSettingGates:
    @pytest.mark.parametrize(
        ("setting_key", "url_name", "needs_auth"),
        GATED_LIST_ENDPOINTS,
    )
    def test_disabled_setting_404s_the_endpoint(
        self, setting_key, url_name, needs_auth
    ):
        client = APIClient()
        if needs_auth:
            client.force_authenticate(user=UserAccountFactory())

        with _settings_off(setting_key):
            response = client.get(reverse(url_name))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        ("setting_key", "url_name", "needs_auth"),
        GATED_LIST_ENDPOINTS,
    )
    def test_enabled_setting_serves_the_endpoint(
        self, setting_key, url_name, needs_auth
    ):
        client = APIClient()
        if needs_auth:
            client.force_authenticate(user=UserAccountFactory())

        response = client.get(reverse(url_name))

        assert response.status_code == status.HTTP_200_OK

    def test_feedback_gate(self):
        client = APIClient()
        url = reverse("feedback")
        with _settings_off("FEEDBACK_ENABLED"):
            response = client.post(url, {"message": "hi"}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_agent_surface_gated_by_runtime_setting(self):
        """The agent-commerce runtime gate fires BEFORE token auth:
        disabled -> 404 (route hidden); enabled -> the usual 401/403
        for an unauthenticated call."""
        client = APIClient()
        url = reverse("agent-me")

        with _settings_off("AGENT_COMMERCE_ENABLED"):
            response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

        response = client.get(url)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_unsubscribe_token_views_stay_open(self):
        """Links in already-sent emails keep working after the feature
        is turned off — deliberately ungated (bad token -> 4xx, never
        the gate's 404-with-empty-body)."""
        client = APIClient()
        with _settings_off("NEWSLETTER_ENABLED"):
            response = client.get(
                reverse(
                    "user-subscription-confirm-by-token",
                    args=["not-a-real-token"],
                )
            )
        # The view answers (invalid token), the gate does not.
        assert response.status_code != status.HTTP_404_NOT_FOUND


class TestNotificationTaskGuards:
    def test_favourites_driven_tasks_skip_when_disabled(self):
        from product.factories.product import ProductFactory
        from product.tasks import (
            notify_back_in_stock_favourites_live,
            send_price_drop_notifications,
        )

        product = ProductFactory(num_images=0, num_reviews=0)
        with _settings_off("FAVOURITES_ENABLED"):
            live = notify_back_in_stock_favourites_live.apply(
                args=[product.id]
            ).result
            drop = send_price_drop_notifications.apply(
                args=[product.id, 10.0, 5.0]
            ).result

        assert live["reason"] == "favourites_disabled"
        assert drop["reason"] == "favourites_disabled"

    def test_alert_tasks_skip_when_disabled(self):
        from product.factories.product import ProductFactory
        from product.tasks import (
            send_product_alert_price_drop,
            send_product_alert_restock,
        )

        product = ProductFactory(num_images=0, num_reviews=0)
        with _settings_off("PRODUCT_ALERTS_ENABLED"):
            restock = send_product_alert_restock.apply(args=[product.id]).result
            drop = send_product_alert_price_drop.apply(
                args=[product.id, 5.0]
            ).result

        assert restock["reason"] == "product_alerts_disabled"
        assert drop["reason"] == "product_alerts_disabled"


class TestTenantConfigAgentFlags:
    def test_effective_flags_combine_plan_and_settings(self):
        from tenant.models import Tenant
        from tenant.serializers import TenantConfigSerializer

        tenant = Tenant(
            schema_name="public",
            name="t",
            agent_commerce_enabled=True,
        )
        serializer = TenantConfigSerializer()

        assert serializer.get_agent_commerce_enabled(tenant) is True
        assert serializer.get_product_feeds_enabled(tenant) is True

        with _settings_off("PRODUCT_FEEDS_ENABLED"):
            assert serializer.get_agent_commerce_enabled(tenant) is True
            assert serializer.get_product_feeds_enabled(tenant) is False

        with _settings_off("AGENT_COMMERCE_ENABLED"):
            assert serializer.get_agent_commerce_enabled(tenant) is False
            # Feeds are subordinate to the agent-commerce gate.
            assert serializer.get_product_feeds_enabled(tenant) is False

        tenant.agent_commerce_enabled = False
        assert serializer.get_agent_commerce_enabled(tenant) is False
        assert serializer.get_product_feeds_enabled(tenant) is False

    def test_agent_payment_instruments_lists_active_offline_pay_ways(self):
        """Only pay-ways an agent can settle unaided are advertised.

        Online methods send the buyer to the PSP to authenticate, which
        UCP models as an escalation rather than a payment handler, so
        they must never appear in this list.
        """
        from pay_way.factories import PayWayFactory
        from pay_way.models import PayWay
        from tenant.models import Tenant
        from tenant.serializers import TenantConfigSerializer

        # Seeded data already includes an offline cash-on-delivery row, so
        # start from a known table to assert on exact contents.
        PayWay.objects.all().delete()

        PayWayFactory(
            active=True,
            is_online_payment=False,
            provider_code="cash_on_delivery",
        )
        # A second row sharing the code must still yield ONE instrument:
        # a merchant may offer cash on delivery through two carriers.
        PayWayFactory(
            active=True,
            is_online_payment=False,
            provider_code="cash_on_delivery",
        )
        PayWayFactory(
            active=True, is_online_payment=True, provider_code="viva_wallet"
        )
        PayWayFactory(
            active=False, is_online_payment=False, provider_code="bank_wire"
        )
        # A provider-less row is unaddressable — an agent has nothing to
        # name when submitting the instrument.
        PayWayFactory(active=True, is_online_payment=False, provider_code="")

        tenant = Tenant(
            schema_name="public", name="t", agent_commerce_enabled=True
        )
        serializer = TenantConfigSerializer()

        assert serializer.get_agent_payment_instruments(tenant) == [
            "cash_on_delivery"
        ]

    def test_agent_payment_instruments_empty_when_surface_off(self):
        from pay_way.factories import PayWayFactory
        from pay_way.models import PayWay
        from tenant.models import Tenant
        from tenant.serializers import TenantConfigSerializer

        # Seeded data already includes an offline cash-on-delivery row, so
        # start from a known table to assert on exact contents.
        PayWay.objects.all().delete()

        PayWayFactory(
            active=True,
            is_online_payment=False,
            provider_code="cash_on_delivery",
        )
        serializer = TenantConfigSerializer()

        # Plan flag off.
        off = Tenant(
            schema_name="public", name="t", agent_commerce_enabled=False
        )
        assert serializer.get_agent_payment_instruments(off) == []

        # Plan flag on, merchant extra-setting off.
        on = Tenant(schema_name="public", name="t", agent_commerce_enabled=True)
        with _settings_off("AGENT_COMMERCE_ENABLED"):
            assert serializer.get_agent_payment_instruments(on) == []

    def test_hosted_payment_gate_needs_both_tiers(self):
        """Either tier alone can withdraw the feature.

        The platform flag is the plan tier and the extra-setting is the
        merchant tier; the effective value is the AND, and both are
        subordinate to agent commerce.
        """
        from tenant.models import Tenant
        from tenant.serializers import TenantConfigSerializer

        serializer = TenantConfigSerializer()
        on = Tenant(
            schema_name="public",
            name="t",
            agent_commerce_enabled=True,
            agent_hosted_payment_enabled=True,
        )
        assert serializer.get_agent_hosted_payment_enabled(on) is True

        # Merchant tier off.
        with _settings_off("AGENT_HOSTED_PAYMENT_ENABLED"):
            assert serializer.get_agent_hosted_payment_enabled(on) is False

        # Platform tier off.
        platform_off = Tenant(
            schema_name="public",
            name="t",
            agent_commerce_enabled=True,
            agent_hosted_payment_enabled=False,
        )
        assert (
            serializer.get_agent_hosted_payment_enabled(platform_off) is False
        )

        # Subordinate to agent commerce: the surface being off withdraws
        # it regardless of either payment tier.
        with _settings_off("AGENT_COMMERCE_ENABLED"):
            assert serializer.get_agent_hosted_payment_enabled(on) is False

        commerce_off = Tenant(
            schema_name="public",
            name="t",
            agent_commerce_enabled=False,
            agent_hosted_payment_enabled=True,
        )
        assert (
            serializer.get_agent_hosted_payment_enabled(commerce_off) is False
        )
