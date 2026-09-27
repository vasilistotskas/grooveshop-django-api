"""``0011_convert_legacy_pricing_to_rates`` — the release-N data migration
that turns the six legacy Setting rows into ``ShippingRate`` rows.

Verifies:
* Existing Setting values (or their frozen historical defaults when
  the row is missing) land on the right (provider, country, kind).
* Countries come from ``metadata['supported_countries']``, else GR.
* A provider that doesn't support a kind gets no rate for it.
* Idempotent re-run, and reverse deletes what it created.
"""

from __future__ import annotations

import importlib
from decimal import Decimal

import pytest

from shipping.models import ShippingProvider, ShippingRate

MIGRATION = "shipping.migrations.0011_convert_legacy_pricing_to_rates"


class _SchemaEditor:
    class connection:
        alias = "default"


@pytest.fixture
def convert():
    module = importlib.import_module(MIGRATION)

    def _run():
        from django.apps import apps

        module.convert_legacy_pricing(apps, _SchemaEditor)

    return _run


@pytest.fixture
def unconvert():
    module = importlib.import_module(MIGRATION)

    def _run():
        from django.apps import apps

        module.unconvert_legacy_pricing(apps, _SchemaEditor)

    return _run


def _set_decimal_setting(name: str, value: str) -> None:
    from extra_settings.models import Setting

    Setting.objects.update_or_create(
        name=name,
        defaults={"value_type": Setting.TYPE_DECIMAL, "value_decimal": value},
    )


@pytest.mark.django_db
class TestFrozenHistoricalDefaults:
    """A tenant that never touched its Setting rows still gets a rate
    at the exact numbers the platform has always defaulted to."""

    def test_flat_rate_gr_home_delivery(self, convert):
        from extra_settings.models import Setting

        Setting.objects.filter(
            name__in=["CHECKOUT_SHIPPING_PRICE", "FREE_SHIPPING_THRESHOLD"]
        ).delete()

        convert()

        rate = ShippingRate.objects.get(
            provider__code="flat_rate", country_id="GR", kind="home_delivery"
        )
        assert rate.price.amount == Decimal("3.00")
        assert rate.free_shipping_threshold.amount == Decimal("50.00")
        assert rate.is_active is True

    def test_boxnow_gr_pickup_point(self, convert):
        from extra_settings.models import Setting

        Setting.objects.filter(
            name__in=[
                "BOXNOW_SHIPPING_PRICE",
                "BOXNOW_FREE_SHIPPING_THRESHOLD",
            ]
        ).delete()

        convert()

        rate = ShippingRate.objects.get(
            provider__code="boxnow", country_id="GR", kind="pickup_point"
        )
        assert rate.price.amount == Decimal("2.50")
        assert rate.free_shipping_threshold.amount == Decimal("30.00")

    def test_acs_gr_both_kinds(self, convert):
        from extra_settings.models import Setting

        Setting.objects.filter(
            name__in=["ACS_SHIPPING_PRICE", "ACS_FREE_SHIPPING_THRESHOLD"]
        ).delete()

        convert()

        for kind in ("home_delivery", "pickup_point"):
            rate = ShippingRate.objects.get(
                provider__code="acs", country_id="GR", kind=kind
            )
            assert rate.price.amount == Decimal("3.50")
            assert rate.free_shipping_threshold.amount == Decimal("40.00")


@pytest.mark.django_db
class TestExistingSettingValuesAreCopiedVerbatim:
    def test_a_retuned_flat_rate_setting_is_copied(self, convert):
        # The autouse ``_reseed_shipping_providers`` fixture already ran
        # ``convert_legacy_pricing`` once (before this test body) at
        # whatever Setting values existed then — clear the row it
        # created so THIS test's ``get_or_create`` sees a fresh insert
        # at the values set below, not a no-op against the old one.
        ShippingRate.objects.filter(
            provider__code="flat_rate", country_id="GR"
        ).delete()
        _set_decimal_setting("CHECKOUT_SHIPPING_PRICE", "4.50")
        _set_decimal_setting("FREE_SHIPPING_THRESHOLD", "45.00")

        convert()

        rate = ShippingRate.objects.get(
            provider__code="flat_rate", country_id="GR", kind="home_delivery"
        )
        assert rate.price.amount == Decimal("4.50")
        assert rate.free_shipping_threshold.amount == Decimal("45.00")


@pytest.mark.django_db
class TestCountryScope:
    def test_defaults_to_gr_when_supported_countries_is_absent(self, convert):
        convert()
        codes = set(
            ShippingRate.objects.filter(provider__code="flat_rate").values_list(
                "country_id", flat=True
            )
        )
        assert codes == {"GR"}

    def test_uses_metadata_supported_countries_when_present(self, convert):
        from country.models import Country

        Country.objects.get_or_create(
            alpha_2="CY",
            defaults={"alpha_3": "CYP", "phone_code": 357, "sort_order": 1},
        )

        provider = ShippingProvider.objects.get(code="boxnow")
        provider.metadata = {
            **provider.metadata,
            "supported_countries": ["GR", "CY"],
        }
        provider.save(update_fields=["metadata"])

        convert()

        codes = set(
            ShippingRate.objects.filter(provider=provider).values_list(
                "country_id", flat=True
            )
        )
        assert codes == {"GR", "CY"}

    def test_a_country_code_with_no_matching_row_is_skipped(self, convert):
        provider = ShippingProvider.objects.get(code="boxnow")
        provider.metadata = {
            **provider.metadata,
            "supported_countries": ["ZZ"],
        }
        provider.save(update_fields=["metadata"])

        convert()

        assert not ShippingRate.objects.filter(
            provider=provider, country_id="ZZ"
        ).exists()


@pytest.mark.django_db
class TestKindSupportIsRespected:
    def test_boxnow_gets_no_home_delivery_rate(self, convert):
        convert()
        assert not ShippingRate.objects.filter(
            provider__code="boxnow", kind="home_delivery"
        ).exists()

    def test_flat_rate_gets_no_pickup_point_rate(self, convert):
        convert()
        assert not ShippingRate.objects.filter(
            provider__code="flat_rate", kind="pickup_point"
        ).exists()


@pytest.mark.django_db
class TestIdempotencyAndReverse:
    def test_running_twice_does_not_duplicate_rows(self, convert):
        convert()
        convert()
        assert (
            ShippingRate.objects.filter(
                provider__code="flat_rate", country_id="GR"
            ).count()
            == 1
        )

    def test_reverse_deletes_the_converted_rows(self, convert, unconvert):
        convert()
        assert ShippingRate.objects.filter(
            provider__code__in=["flat_rate", "boxnow", "acs"]
        ).exists()

        unconvert()

        assert not ShippingRate.objects.filter(
            provider__code__in=["flat_rate", "boxnow", "acs"]
        ).exists()

    def test_does_not_touch_an_operator_edited_rate(self, convert):
        convert()
        rate = ShippingRate.objects.get(
            provider__code="flat_rate", country_id="GR", kind="home_delivery"
        )
        rate.price = "9.99"
        rate.save(update_fields=["price"])

        convert()

        rate.refresh_from_db()
        assert rate.price.amount == Decimal("9.99")
