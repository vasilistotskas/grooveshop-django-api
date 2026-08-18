"""Tests for ``meta_capi.services._success_url_for_order`` — TASK D.

``event_source_url`` must resolve to the ACTIVE TENANT's storefront
domain (via ``get_tenant_frontend_url``), not the platform-wide
``settings.NUXT_BASE_URL``, so a tenant-B order reports back to
tenant-B's domain instead of webside.gr.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db import connection
from django.test import override_settings

from meta_capi.services import _success_url_for_order
from order.factories.order import OrderFactory


@pytest.fixture
def bind_tenant(monkeypatch):
    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)

    yield _bind


def _fake_tenant(primary_domain: str, schema_name: str = "tenant_a"):
    """Build the minimum tenant shape the tenant_urls helpers touch."""
    primary_domain_obj = SimpleNamespace(domain=primary_domain)
    domains_qs = MagicMock()
    domains_qs.filter.return_value.first.return_value = primary_domain_obj
    return SimpleNamespace(schema_name=schema_name, domains=domains_qs)


@pytest.mark.django_db
class TestSuccessUrlForOrder:
    def test_uses_tenant_domain_not_platform_nuxt_base_url(self, bind_tenant):
        order = OrderFactory(num_order_items=0)
        bind_tenant(_fake_tenant(primary_domain="tenant-b.com"))

        with override_settings(NUXT_BASE_URL="https://webside.gr"):
            url = _success_url_for_order(order)

        assert url == f"https://tenant-b.com/checkout/success/{order.uuid}"

    def test_falls_back_to_settings_when_no_tenant(self, bind_tenant):
        order = OrderFactory(num_order_items=0)
        bind_tenant(None)

        with override_settings(NUXT_BASE_URL="https://webside.gr"):
            url = _success_url_for_order(order)

        assert url == f"https://webside.gr/checkout/success/{order.uuid}"

    def test_returns_empty_string_when_no_base_url_configured(
        self, bind_tenant
    ):
        order = OrderFactory(num_order_items=0)
        bind_tenant(None)

        with override_settings(NUXT_BASE_URL=""):
            url = _success_url_for_order(order)

        assert url == ""
