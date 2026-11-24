"""``0023_shipping_rate_zone`` puts every rate in a zone covering its whole
country, so what each store serves and charges does not change, and its
reverse gives every rate its country back.

Drives the real ``MigrationExecutor`` with rows written through the
historical models (``tests/integration/pay_way/test_migration_key.py`` is
the same harness). PostgreSQL DDL is transactional, so the ``TestCase``
transaction leaves the worker's database at the latest migration again.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import connection
from django.test import TestCase

from tests.utils.migrations import migrate_to

BEFORE = ("shipping", "0022_shipping_zone")
AFTER = ("shipping", "0023_shipping_rate_zone")


def _settle_deferred_constraints():
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class TestRatesMoveIntoZones(TestCase):
    def _rates_before(self, *countries: str) -> None:
        apps = migrate_to(BEFORE)
        Country = apps.get_model("country", "Country")
        Provider = apps.get_model("shipping", "ShippingProvider")
        Rate = apps.get_model("shipping", "ShippingRate")
        Rate.objects.all().delete()
        provider = Provider.objects.get(code="flat_rate")
        for code in countries:
            Country.objects.get_or_create(
                alpha_2=code,
                defaults={"alpha_3": f"{code}X", "sort_order": 0},
            )
            for kind, price in (("home_delivery", "3.00"),):
                Rate.objects.create(
                    provider=provider,
                    country_id=code,
                    kind=kind,
                    price=Decimal(price),
                )
        _settle_deferred_constraints()

    def test_each_country_gets_one_whole_country_zone(self):
        self._rates_before("GR", "QZ")

        apps = migrate_to(AFTER)
        Zone = apps.get_model("shipping", "ShippingZone")
        Rate = apps.get_model("shipping", "ShippingRate")

        zones = {zone.country_id: zone for zone in Zone.objects.all()}
        self.assertEqual(set(zones), {"GR", "QZ"})
        self.assertFalse(
            apps.get_model("shipping", "ShippingZoneRegion").objects.exists()
        )
        for rate in Rate.objects.all():
            self.assertEqual(rate.zone_id, zones[rate.country_id].pk)

    def test_a_zone_is_named_after_its_country(self):
        self._rates_before("GR")

        apps = migrate_to(AFTER)
        Zone = apps.get_model("shipping", "ShippingZone")
        Translation = apps.get_model("country", "CountryTranslation")

        name = (
            Translation.objects.filter(master_id="GR", language_code="el")
            .values_list("name", flat=True)
            .first()
        )
        self.assertEqual(Zone.objects.get(country_id="GR").name, name or "GR")

    def test_the_reverse_gives_every_rate_its_country_back(self):
        self._rates_before("GR")
        apps = migrate_to(AFTER)
        Zone = apps.get_model("shipping", "ShippingZone")
        Rate = apps.get_model("shipping", "ShippingRate")
        Provider = apps.get_model("shipping", "ShippingProvider")
        # A rate the new code wrote: a zone, no country.
        Rate.objects.create(
            provider=Provider.objects.get(code="boxnow"),
            zone=Zone.objects.get(country_id="GR"),
            kind="pickup_point",
            price=Decimal("2.50"),
        )
        _settle_deferred_constraints()

        apps = migrate_to(BEFORE)
        Rate = apps.get_model("shipping", "ShippingRate")

        self.assertEqual(
            set(Rate.objects.values_list("country_id", flat=True)), {"GR"}
        )
        self.assertEqual(Rate.objects.count(), 2)
