"""Unit tests for ``ShippingRateInline`` on ``ShippingProviderAdmin``."""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite

from country.factories import CountryFactory
from shipping.admin import ShippingProviderAdmin, ShippingRateInline
from shipping.factories import ShippingProviderFactory, ShippingRateFactory
from shipping.models import ShippingProvider

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_instance():
    return ShippingProviderAdmin(ShippingProvider, AdminSite())


class TestShippingRateInline:
    def test_country_is_an_autocomplete(self):
        """Countries are reference data every store role may view
        (``tenant.role_scopes.REFERENCE_DATA_APP_LABELS``), so the
        country admin's autocomplete answers on a store host."""
        assert "country" in ShippingRateInline.autocomplete_fields

    def test_fields_cover_the_editable_columns(self):
        assert ShippingRateInline.fields == (
            "country",
            "kind",
            "price",
            "free_shipping_threshold",
            "max_weight_grams",
            "is_active",
        )

    def test_model_is_shipping_rate(self):
        from shipping.models import ShippingRate

        assert ShippingRateInline.model is ShippingRate

    def test_registered_on_the_provider_admin(self, admin_instance):
        assert ShippingRateInline in admin_instance.inlines


class TestRatesCount:
    def test_counts_the_providers_own_rates(self, admin_instance):
        provider = ShippingProviderFactory(
            code="rate_admin_count_test",
            supports_home_delivery=True,
            supports_pickup_point=True,
        )
        country_a = CountryFactory()
        country_b = CountryFactory()
        ShippingRateFactory(provider=provider, country=country_a)
        ShippingRateFactory(
            provider=provider, country=country_b, kind="pickup_point"
        )

        annotated = admin_instance.get_queryset(None).get(pk=provider.pk)
        assert admin_instance.rates_count(annotated) == 2

    def test_zero_when_the_provider_has_no_rates(self, admin_instance):
        provider = ShippingProviderFactory(code="rate_admin_zero_test")

        annotated = admin_instance.get_queryset(None).get(pk=provider.pk)
        assert admin_instance.rates_count(annotated) == 0

    def test_does_not_count_another_providers_rates(self, admin_instance):
        provider_a = ShippingProviderFactory(code="rate_admin_a_test")
        provider_b = ShippingProviderFactory(code="rate_admin_b_test")
        country = CountryFactory()
        ShippingRateFactory(provider=provider_a, country=country)

        annotated = admin_instance.get_queryset(None).get(pk=provider_b.pk)
        assert admin_instance.rates_count(annotated) == 0
