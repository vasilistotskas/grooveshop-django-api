"""``0024_shipping_rate_zone_backfill`` puts every rate in a zone covering
its whole country, so what each store serves and charges does not change,
and its reverse gives every rate its country back.

Drives the real ``MigrationExecutor`` with rows written through the
historical models (``tests/integration/pay_way/test_migration_key.py`` is
the same harness). PostgreSQL DDL is transactional, so the ``TestCase``
transaction leaves the worker's database at the latest migration again.
"""

from __future__ import annotations

from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from tests.utils.migrations import migrate_to

BEFORE = ("shipping", "0022_shipping_zone")
AFTER = ("shipping", "0024_shipping_rate_zone_backfill")


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
        apps = migrate_to(BEFORE)
        rates_before = sorted(
            apps.get_model("shipping", "ShippingRate").objects.values_list(
                "pk", "country_id", "kind", "price"
            )
        )

        apps = migrate_to(AFTER)
        Zone = apps.get_model("shipping", "ShippingZone")
        Rate = apps.get_model("shipping", "ShippingRate")

        self.assertEqual(
            sorted(Zone.objects.values_list("country_id", flat=True)),
            ["GR", "QZ"],
        )
        self.assertFalse(
            apps.get_model("shipping", "ShippingZoneRegion").objects.exists()
        )
        zones = dict(Zone.objects.values_list("country_id", "pk"))
        self.assertEqual(
            sorted(
                Rate.objects.values_list(
                    "pk", "country_id", "kind", "price", "zone_id"
                )
            ),
            sorted((*rate, zones[rate[1]]) for rate in rates_before),
        )

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

    def test_the_reverse_refuses_rates_a_region_zone_holds(self):
        # The old shape has one rate per (provider, country, kind): a
        # region's own price has nowhere to go.
        self._rates_before("GR")
        apps = migrate_to(AFTER)
        Zone = apps.get_model("shipping", "ShippingZone")
        ZoneRegion = apps.get_model("shipping", "ShippingZoneRegion")
        Region = apps.get_model("region", "Region")
        Rate = apps.get_model("shipping", "ShippingRate")
        Provider = apps.get_model("shipping", "ShippingProvider")
        region, _ = Region.objects.get_or_create(
            alpha="GR-QZ", defaults={"country_id": "GR", "sort_order": 0}
        )
        crete = Zone.objects.create(name="Crete", country_id="GR")
        ZoneRegion.objects.create(zone=crete, region=region)
        Rate.objects.create(
            provider=Provider.objects.get(code="flat_rate"),
            zone=crete,
            kind="home_delivery",
            price=Decimal("6.00"),
        )
        _settle_deferred_constraints()

        with self.assertRaisesMessage(RuntimeError, "Crete"):
            migrate_to(BEFORE)


def _backfill():
    """``0024``'s forward function, as the contract release runs it
    again over the rates the release before inserted without a zone."""
    return import_module(
        "shipping.migrations.0024_shipping_rate_zone_backfill"
    ).zone_every_rate


def _country_name(apps, code: str) -> str:
    Translation = apps.get_model("country", "CountryTranslation")
    name = (
        Translation.objects.filter(master_id=code, language_code="el")
        .values_list("name", flat=True)
        .first()
    )
    return name or code


class TestTheBackfillIsSafeToRunAgain(TestCase):
    def test_a_name_another_zone_has_takes_the_country_code(self):
        apps = migrate_to(BEFORE)
        Country = apps.get_model("country", "Country")
        Zone = apps.get_model("shipping", "ShippingZone")
        Provider = apps.get_model("shipping", "ShippingProvider")
        Rate = apps.get_model("shipping", "ShippingRate")
        Rate.objects.all().delete()
        Country.objects.get_or_create(
            alpha_2="QZ", defaults={"alpha_3": "QZX", "sort_order": 0}
        )
        name = _country_name(apps, "GR")
        Zone.objects.create(name=name, country_id="QZ")
        Rate.objects.create(
            provider=Provider.objects.get(code="flat_rate"),
            country_id="GR",
            kind="home_delivery",
            price=Decimal("3.00"),
        )
        _settle_deferred_constraints()

        apps = migrate_to(AFTER)
        Zone = apps.get_model("shipping", "ShippingZone")

        self.assertEqual(Zone.objects.get(country_id="GR").name, f"{name} (GR)")

    def test_a_country_with_two_base_zones_uses_the_oldest(self):
        apps = migrate_to(BEFORE)
        Zone = apps.get_model("shipping", "ShippingZone")
        Provider = apps.get_model("shipping", "ShippingProvider")
        Rate = apps.get_model("shipping", "ShippingRate")
        Rate.objects.all().delete()
        oldest = Zone.objects.create(name="Greece", country_id="GR")
        Zone.objects.create(name="Greece again", country_id="GR")
        Rate.objects.create(
            provider=Provider.objects.get(code="flat_rate"),
            country_id="GR",
            kind="home_delivery",
            price=Decimal("3.00"),
        )
        _settle_deferred_constraints()

        apps = migrate_to(AFTER)
        Rate = apps.get_model("shipping", "ShippingRate")

        self.assertEqual(
            list(Rate.objects.values_list("zone_id", flat=True)), [oldest.pk]
        )

    def test_a_rate_its_base_zone_already_prices_is_deleted(self):
        apps = migrate_to(AFTER)
        Zone = apps.get_model("shipping", "ShippingZone")
        Provider = apps.get_model("shipping", "ShippingProvider")
        Rate = apps.get_model("shipping", "ShippingRate")
        Rate.objects.all().delete()
        flat_rate = Provider.objects.get(code="flat_rate")
        greece = Zone.objects.filter(
            country_id="GR", regions__isnull=True
        ).first() or Zone.objects.create(name="Greece", country_id="GR")
        kept = Rate.objects.create(
            provider=flat_rate,
            zone=greece,
            kind="home_delivery",
            price=Decimal("3.00"),
        )
        # What the release before inserts during the rollout: a country
        # and no zone, for a (provider, kind) the base zone already has.
        Rate.objects.create(
            provider=flat_rate,
            country_id="GR",
            kind="home_delivery",
            price=Decimal("4.00"),
        )
        _settle_deferred_constraints()

        with self.assertLogs(
            "shipping.migrations.0024_shipping_rate_zone_backfill", "WARNING"
        ):
            _backfill()(apps, SimpleNamespace(connection=connection))

        self.assertEqual(
            list(Rate.objects.values_list("pk", flat=True)), [kept.pk]
        )


class TestTheContractRerun(TestCase):
    """What the release before writes during the rollout, as the contract
    release finds it when it runs ``0024``'s function again."""

    def _greece(self, apps):
        Zone = apps.get_model("shipping", "ShippingZone")
        Rate = apps.get_model("shipping", "ShippingRate")
        Rate.objects.all().delete()
        return Zone.objects.filter(
            country_id="GR", regions__isnull=True
        ).first() or Zone.objects.create(name="Greece", country_id="GR")

    def test_a_rate_re_pointed_to_another_country_moves_there(self):
        apps = migrate_to(AFTER)
        Country = apps.get_model("country", "Country")
        Provider = apps.get_model("shipping", "ShippingProvider")
        Rate = apps.get_model("shipping", "ShippingRate")
        greece = self._greece(apps)
        Country.objects.get_or_create(
            alpha_2="QZ", defaults={"alpha_3": "QZX", "sort_order": 0}
        )
        # Greece's base zone, but the release before set it to QZ.
        rate = Rate.objects.create(
            provider=Provider.objects.get(code="flat_rate"),
            zone=greece,
            country_id="QZ",
            kind="home_delivery",
            price=Decimal("3.00"),
        )
        _settle_deferred_constraints()

        _backfill()(apps, SimpleNamespace(connection=connection))

        rate.refresh_from_db()
        self.assertEqual(rate.zone.country_id, "QZ")
        self.assertEqual(rate.country_id, rate.zone.country_id)
        self.assertNotEqual(rate.zone_id, greece.pk)

    def test_a_history_row_gets_its_countrys_base_zone(self):
        apps = migrate_to(AFTER)
        History = apps.get_model("shipping", "HistoricalShippingRate")
        Provider = apps.get_model("shipping", "ShippingProvider")
        greece = self._greece(apps)
        History.objects.all().delete()
        History.objects.create(
            id=1,
            provider_id=Provider.objects.get(code="flat_rate").pk,
            country_id="GR",
            kind="home_delivery",
            price=Decimal("3.00"),
            price_currency="EUR",
            free_shipping_threshold_currency="EUR",
            is_active=True,
            created_at=timezone.now(),
            updated_at=timezone.now(),
            history_date=timezone.now(),
            history_type="~",
        )

        _backfill()(apps, SimpleNamespace(connection=connection))

        self.assertEqual(
            list(History.objects.values_list("zone_id", flat=True)),
            [greece.pk],
        )


class TestTheZoneBecomesRequired(TestCase):
    """``0026`` and ``0027``, as a store meets them."""

    def test_a_zone_less_rate_is_zoned_and_the_column_is_tightened(self):
        apps = migrate_to(("shipping", "0025_shipping_rate_zone_required"))
        Rate = apps.get_model("shipping", "ShippingRate")
        Rate.objects.all().delete()
        provider = apps.get_model("shipping", "ShippingProvider").objects.get(
            code="flat_rate"
        )
        with connection.cursor() as cursor:
            # The release before 0022 inserts no zone.
            cursor.execute(
                "INSERT INTO shipping_shippingrate (provider_id, country_id, "
                "kind, price, price_currency, "
                "free_shipping_threshold_currency, is_active, created_at, "
                "updated_at) VALUES (%s, 'GR', 'home_delivery', 3, 'EUR', "
                "'EUR', true, now(), now())",
                [provider.pk],
            )

        apps = migrate_to(
            ("shipping", "0027_shipping_rate_country_out_of_state")
        )

        Rate = apps.get_model("shipping", "ShippingRate")
        self.assertEqual(Rate.objects.get().zone.country_id, "GR")
        self.assertFalse(
            any(field.name == "country" for field in Rate._meta.get_fields())
        )
        with connection.cursor() as cursor:
            columns = {
                column.name: column.null_ok
                for column in connection.introspection.get_table_description(
                    cursor, "shipping_shippingrate"
                )
            }
        self.assertFalse(columns["zone_id"])
        # Kept for the release still serving, and free to be NULL.
        self.assertTrue(columns["country_id"])
