"""``/tenant/resolve`` advertises the store's active carriers.

The footer's "Delivered by" badges read ``shippingCarriers``. The staff
only ``shipping/providers`` endpoint stays staff-only; this payload
carries a code and a display name per ACTIVE carrier and nothing else.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test.client import Client
from django_tenants.utils import get_public_schema_name

from shipping.factories import ShippingProviderFactory
from shipping.models import ShippingProvider
from tenant.cache import tenant_resolve_key
from tenant.models import Tenant, TenantDomain
from tenant.serializers import TenantConfigSerializer
from tests.utils.staff import store_tenant


def _carriers(tenant: Tenant) -> list[dict]:
    """The field alone: the rest of the payload needs a saved tenant."""
    field = TenantConfigSerializer().fields["shipping_carriers"]
    return field.to_representation(field.get_attribute(tenant))


@pytest.mark.django_db
class TestShippingCarriersPayload:
    def test_only_active_carriers_with_code_and_name(self):
        ShippingProvider.objects.all().delete()
        ShippingProviderFactory(
            code="acs", name="ACS Courier", priority=1, is_active=True
        )
        ShippingProviderFactory(
            code="boxnow", name="BOX NOW", priority=2, is_active=True
        )
        ShippingProviderFactory(
            code="dormant", name="Dormant", priority=0, is_active=False
        )

        carriers = _carriers(Tenant(schema_name="acme", name="t"))

        assert carriers == [
            {"code": "acs", "name": "ACS Courier"},
            {"code": "boxnow", "name": "BOX NOW"},
        ]

    def test_no_active_carrier_is_an_empty_list(self):
        ShippingProvider.objects.all().delete()

        assert _carriers(Tenant(schema_name="acme", name="t")) == []

    def test_the_control_plane_advertises_none(self):
        """The public schema has no ``ShippingProvider`` table.

        The differential is the point: an active carrier exists, and
        only the schema decides whether it is advertised.
        """
        ShippingProvider.objects.all().delete()
        ShippingProviderFactory(code="acs", name="ACS", is_active=True)

        control_plane = Tenant(
            schema_name=get_public_schema_name(), name="Public (Platform)"
        )

        store = Tenant(schema_name="acme", name="t")
        assert _carriers(store) == [{"code": "acs", "name": "ACS"}]
        assert _carriers(control_plane) == []

    def test_the_resolve_endpoint_serves_it_camel_cased(self):
        ShippingProvider.objects.all().delete()
        ShippingProviderFactory(code="acs", name="ACS Courier", is_active=True)
        tenant = store_tenant("resolve_carriers")
        TenantDomain.objects.create(
            tenant=tenant, domain="resolve-carriers.example", is_primary=True
        )

        response = Client().get(
            "/api/v1/tenant/resolve?domain=resolve-carriers.example"
        )

        assert response.status_code == 200
        assert response.json()["shippingCarriers"] == [
            {"code": "acs", "name": "ACS Courier"}
        ]


@pytest.mark.django_db
class TestShippingCarriersInvalidateTheResolveCache:
    def test_saving_and_deleting_a_carrier_reach_the_receiver(self):
        with patch(
            "tenant.signals._bump_generation_for_current_schema"
        ) as bump:
            provider = ShippingProviderFactory(code="cache_probe")
            assert bump.called, "post_save receiver not connected"

            bump.reset_mock()
            provider.delete()
            assert bump.called, "post_delete receiver not connected"

    def test_a_carrier_change_moves_the_resolve_key_of_its_tenant_only(self):
        from tenant.signals import bump_generation_on_shipping_provider_change

        tenant = store_tenant("gen_carrier")
        bystander = store_tenant("gen_carrier_bystander")
        before = tenant_resolve_key("d.example", tenant)

        with patch("tenant.signals.connection") as conn:
            conn.schema_name = tenant.schema_name
            bump_generation_on_shipping_provider_change(None, None)

        tenant.refresh_from_db()
        bystander.refresh_from_db()
        assert tenant_resolve_key("d.example", tenant) != before
        assert bystander.cache_generation == 0
