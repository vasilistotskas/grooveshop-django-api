"""A public country or region a store still uses cannot be deleted.

``Country`` and ``Region`` are public (SHARED_APPS) rows every store
reads; the orders, saved addresses, shipping rates and pay-way exclusions
that point at them are in each STORE's schema, where the deletion
collector run from ``public`` cannot see them. These tests run against two
real tenant schemas, which ``tests/`` (public schema only) cannot.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib import admin as django_admin
from django.db.models import ProtectedError
from django.test import RequestFactory
from django_tenants.utils import schema_context

from tenant.deletion import delete_public
from tenant.reference_guard import references_to

pytestmark = pytest.mark.django_db


def _public_country(alpha_2: str):
    from country.models import Country

    with schema_context("public"):
        country, _ = Country.objects.get_or_create(
            alpha_2=alpha_2,
            defaults={"alpha_3": f"{alpha_2}X", "phone_code": 999},
        )
        return country


def _public_region(country, alpha: str):
    from region.models import Region

    with schema_context("public"):
        region, _ = Region.objects.get_or_create(
            alpha=alpha, country=country, defaults={"sort_order": 0}
        )
        return region


def _make_order(country, region):
    from order.factories.order import OrderFactory

    return OrderFactory(country=country, region=region, num_order_items=0)


def _make_address(country, region):
    from user.factories.address import UserAddressFactory

    return UserAddressFactory(country=country, region=region)


def _make_rate(country, region):
    from shipping.factories import ShippingProviderFactory
    from shipping.models import ShippingRate

    return ShippingRate.objects.create(
        provider=ShippingProviderFactory(code="guard_probe"),
        country=country,
        kind="home_delivery",
        price=Decimal("3.00"),
    )


def _make_exclusion(country, region):
    from pay_way.factories import PayWayShippingExclusionFactory

    return PayWayShippingExclusionFactory(country=country)


def _discard(row) -> None:
    """Remove a row for real: a soft-deleted order still holds its FK, so
    go through the plain base manager rather than ``row.delete()``."""
    type(row)._base_manager.filter(pk=row.pk).delete()


COUNTRY_REFERENCES = {
    "order": _make_order,
    "address": _make_address,
    "rate": _make_rate,
    "pay way exclusion": _make_exclusion,
}


@pytest.fixture
def country():
    from country.models import Country

    country = _public_country("ZY")
    yield country
    with schema_context("public"):
        delete_public(Country.objects.filter(pk=country.pk))


def _platform_request():
    from user.factories.account import UserAccountFactory

    request = RequestFactory().get("/admin/")
    request.user = UserAccountFactory(is_staff=True, is_superuser=True)
    return request


def _platform_admin(model):
    from admin.platform_site import platform_admin_site

    return platform_admin_site._registry[model]


@pytest.mark.parametrize("kind", COUNTRY_REFERENCES)
def test_a_country_a_store_uses_is_refused_and_names_the_store(
    mt_tenant, country, kind
):
    from country.models import Country

    with schema_context(mt_tenant.schema_name):
        reference = COUNTRY_REFERENCES[kind](country, None)

    try:
        refs = references_to([country])
        assert [r.store for r in refs] == [mt_tenant.name]

        model_admin = _platform_admin(Country)
        *_, protected = model_admin.get_deleted_objects(
            [country], _platform_request()
        )
        assert any(mt_tenant.name in str(item) for item in protected)
    finally:
        with schema_context(mt_tenant.schema_name):
            _discard(reference)


def test_a_region_a_store_uses_refuses_its_region_and_its_country(
    mt_tenant, country
):
    from region.models import Region

    region = _public_region(country, "ZR")
    with schema_context(mt_tenant.schema_name):
        address = _make_address(None, region)
    try:
        assert [r.store for r in references_to([region])] == [mt_tenant.name]
        # The country takes its regions with it.
        assert [r.store for r in references_to([country])] == [mt_tenant.name]
        *_, protected = _platform_admin(Region).get_deleted_objects(
            [region], _platform_request()
        )
        assert protected
    finally:
        with schema_context(mt_tenant.schema_name):
            address.delete()


def test_another_stores_use_refuses_a_delete_issued_from_this_store(
    mt_tenant, mt_tenant_b, country
):
    """The backstop: deleting from inside store B's schema would null
    B's own rows and leave store A pointing at a vanished country."""
    from country.models import Country

    with schema_context(mt_tenant.schema_name):
        order = _make_order(country, None)
    try:
        with schema_context(mt_tenant_b.schema_name):
            with pytest.raises(ProtectedError, match=mt_tenant.name):
                Country.objects.get(pk=country.pk).delete()
        with schema_context("public"):
            assert Country.objects.filter(pk=country.pk).exists()
    finally:
        with schema_context(mt_tenant.schema_name):
            _discard(order)


def test_a_country_nobody_uses_is_deleted_from_the_platform_admin(
    mt_tenant, mt_tenant_b, country
):
    """Skipping store-only tables is safe because no store uses it. Before
    the guard, even this failed: the stock collector queried
    ``shipping_shippingrate`` in the public schema."""
    from country.models import Country

    model_admin = _platform_admin(Country)
    *_, protected = model_admin.get_deleted_objects(
        [country], _platform_request()
    )
    assert protected == []

    with schema_context("public"):
        model_admin.delete_model(_platform_request(), country)
        assert not Country.objects.filter(pk=country.pk).exists()


def test_a_store_whose_schema_is_gone_cannot_block_a_delete(mt_tenant, country):
    """An offboarded store keeps its row for a while; it holds nothing."""
    from tenant.models import Tenant

    ghost = Tenant(
        schema_name="mt_ghost_store",
        name="Ghost",
        slug="mt-ghost-store",
        owner_email="ghost@example.com",
    )
    ghost.auto_create_schema = False
    with schema_context("public"):
        ghost.save()
    try:
        assert references_to([country]) == []
    finally:
        with schema_context("public"):
            ghost.delete()


def test_a_store_host_cannot_delete_reference_data(mt_tenant, country):
    from country.models import Country

    model_admin = django_admin.site._registry[Country]
    request = _platform_request()
    request.tenant = mt_tenant

    assert not model_admin.has_delete_permission(request, country)
