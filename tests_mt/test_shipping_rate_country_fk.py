"""``ShippingRate`` (TENANT_APPS) referencing ``country.Country``
(SHARED_APPS) — the same cross-schema FK shape ``Order.country``
already uses, exercised under the REAL tenant router the main suite
strips.

Covers:
* The cross-schema FK actually resolves under ``schema_context`` (a
  fresh tenant schema has no ``country_country`` table of its own —
  the FK only works because the tenant's search_path falls through to
  public for it).
* ``shipping_shippingrate`` itself is schema-local: invisible from
  public and from a sibling tenant, mirroring ``test_model_write_
  isolation.py``/``test_b2b_flag_isolation.py``.
* Two tenants can each have their OWN rate for the SAME shared Country
  row without colliding — the whole point of the table.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import transaction
from django.db.utils import ProgrammingError
from django_tenants.utils import schema_context


def _seed_gr(db_alias: str = "default") -> None:
    """Minimal GR row — the real seed migrations already ran once at
    tenant-schema creation, so this is idempotent, not a first write."""
    from country.models import Country

    Country.objects.using(db_alias).get_or_create(
        alpha_2="GR",
        defaults={"alpha_3": "GRC", "phone_code": 30, "sort_order": 0},
    )


@pytest.mark.django_db
def test_rate_resolves_its_cross_schema_country_fk(mt_tenant):
    from country.models import Country
    from shipping.factories import ShippingProviderFactory
    from shipping.models import ShippingRate

    with schema_context("public"):
        _seed_gr()

    with schema_context(mt_tenant.schema_name):
        provider = ShippingProviderFactory(code="mt_rate_probe")
        country = Country.objects.get(alpha_2="GR")
        rate = ShippingRate.objects.create(
            provider=provider,
            country=country,
            kind="home_delivery",
            price=Decimal("3.00"),
        )
        fetched = ShippingRate.objects.select_related("country").get(pk=rate.pk)
        assert fetched.country.alpha_2 == "GR"


@pytest.mark.django_db
def test_rate_rows_are_schema_local(mt_tenant):
    """``shipping_shippingrate`` is TENANT_APPS-only — the table must
    not exist in the public schema AT ALL, the stronger guarantee
    (query itself fails, not just filtered to empty)."""
    from country.models import Country
    from shipping.factories import ShippingProviderFactory
    from shipping.models import ShippingRate

    with schema_context("public"):
        _seed_gr()

    with schema_context(mt_tenant.schema_name):
        provider = ShippingProviderFactory(code="mt_rate_local_probe")
        country = Country.objects.get(alpha_2="GR")
        rate = ShippingRate.objects.create(
            provider=provider,
            country=country,
            kind="home_delivery",
            price=Decimal("3.00"),
        )
        rate_id = rate.pk

    with schema_context("public"):
        with (
            pytest.raises(ProgrammingError, match="does not exist"),
            transaction.atomic(),
        ):
            ShippingRate.objects.filter(pk=rate_id).exists()


@pytest.mark.django_db
def test_two_tenants_can_rate_the_same_country_independently(
    mt_tenant, mt_tenant_b
):
    """The whole point of the table: Country/Region stay global, but
    each store owns its own price/availability for it."""
    from country.models import Country
    from shipping.factories import ShippingProviderFactory
    from shipping.models import ShippingRate

    with schema_context("public"):
        _seed_gr()

    with schema_context(mt_tenant.schema_name):
        provider_a = ShippingProviderFactory(code="mt_rate_tenant_a")
        country_a = Country.objects.get(alpha_2="GR")
        ShippingRate.objects.create(
            provider=provider_a,
            country=country_a,
            kind="home_delivery",
            price=Decimal("3.00"),
        )

    with schema_context(mt_tenant_b.schema_name):
        provider_b = ShippingProviderFactory(code="mt_rate_tenant_b")
        country_b = Country.objects.get(alpha_2="GR")
        ShippingRate.objects.create(
            provider=provider_b,
            country=country_b,
            kind="home_delivery",
            price=Decimal("5.00"),
        )

    with schema_context(mt_tenant.schema_name):
        rate_a = ShippingRate.objects.get(provider__code="mt_rate_tenant_a")
        assert rate_a.price.amount == Decimal("3.00")
        assert not ShippingRate.objects.filter(
            provider__code="mt_rate_tenant_b"
        ).exists()

    with schema_context(mt_tenant_b.schema_name):
        rate_b = ShippingRate.objects.get(provider__code="mt_rate_tenant_b")
        assert rate_b.price.amount == Decimal("5.00")
        assert not ShippingRate.objects.filter(
            provider__code="mt_rate_tenant_a"
        ).exists()
