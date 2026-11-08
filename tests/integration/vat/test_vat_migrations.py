"""The two migrations that make the VAT rate rule physical.

``product`` 0070 (``Product.vat`` NOT NULL, the contract step of 0069) and
``vat`` 0010 (``Vat.value`` unique) both REFUSE rather than repair: a rate is
a tax decision, so neither deletes, merges or guesses a row.

Drives the real ``MigrationExecutor`` (the harness of
``test_migration_amount_due_contract``); PostgreSQL DDL is transactional, so
the ``TestCase`` transaction undoes the schema changes.
"""

from __future__ import annotations

from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

PRODUCT_BEFORE = ("product", "0069_product_vat_required")
PRODUCT_AFTER = ("product", "0070_product_vat_not_null")
VAT_BEFORE = ("vat", "0009_field_verbose_names")
VAT_AFTER = ("vat", "0010_vat_value_unique")


def _migrate_to(*targets):
    executor = MigrationExecutor(connection)
    executor.migrate(list(targets))
    executor.loader.build_graph()
    return executor.loader.project_state(list(targets)).apps


def _vat_id_is_nullable() -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'product_product' AND column_name = 'vat_id'"
        )
        return cursor.fetchone()[0] == "YES"


def _settle_deferred_constraints():
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class TestProductVatNotNull(TestCase):
    def test_it_is_a_contract_of_the_expand_migration(self):
        executor = MigrationExecutor(connection)
        migration = executor.loader.get_migration(*PRODUCT_AFTER)

        self.assertEqual(migration.contract_of, [PRODUCT_BEFORE])

    def test_it_makes_the_column_not_null_and_reverses(self):
        _migrate_to(PRODUCT_BEFORE)
        self.assertTrue(_vat_id_is_nullable())

        _migrate_to(PRODUCT_AFTER)
        self.assertFalse(_vat_id_is_nullable())

        _migrate_to(PRODUCT_BEFORE)
        self.assertTrue(_vat_id_is_nullable())

    def test_it_refuses_while_a_product_has_no_rate(self):
        apps = _migrate_to(PRODUCT_BEFORE)
        Product = apps.get_model("product", "Product")
        pk = Product.objects.create(slug="no-rate", sku="NO-RATE-1").pk
        _settle_deferred_constraints()
        self.assertIsNone(Product.objects.get(pk=pk).vat_id)

        with self.assertRaises(RuntimeError) as refused:
            _migrate_to(PRODUCT_AFTER)

        # The refusal names the product to fix, not a tool to look it up.
        self.assertIn("1 product(s) without", str(refused.exception))
        self.assertIn(f"{pk} (NO-RATE-1)", str(refused.exception))

        self.assertTrue(_vat_id_is_nullable())

    def test_it_names_the_schema_and_leaves_the_data_alone(self):
        apps = _migrate_to(PRODUCT_BEFORE)
        Product = apps.get_model("product", "Product")
        Product.objects.create(slug="no-rate-a")
        Product.objects.create(slug="no-rate-b")
        _settle_deferred_constraints()

        with self.assertRaisesMessage(RuntimeError, "'public' has 2"):
            _migrate_to(PRODUCT_AFTER)

        self.assertEqual(Product.objects.filter(vat__isnull=True).count(), 2)


class TestVatValueUnique(TestCase):
    def test_it_refuses_existing_duplicates_without_changing_them(self):
        Vat = _migrate_to(VAT_BEFORE).get_model("vat", "Vat")
        Vat.objects.all().delete()
        Vat.objects.create(value="24.0")
        Vat.objects.create(value="24.0")
        _settle_deferred_constraints()

        with self.assertRaisesMessage(
            IntegrityError, "vat_value_unique on public.vat_vat"
        ) as caught:
            _migrate_to(VAT_AFTER)

        self.assertIn("24.0", str(caught.exception))
        self.assertEqual(Vat.objects.filter(value="24.0").count(), 2)

    def test_it_applies_to_distinct_rates_and_reverses(self):
        Vat = _migrate_to(VAT_BEFORE).get_model("vat", "Vat")
        Vat.objects.all().delete()
        Vat.objects.create(value="24.0")
        Vat.objects.create(value="13.0")
        _settle_deferred_constraints()

        Vat = _migrate_to(VAT_AFTER).get_model("vat", "Vat")
        self.assertEqual(Vat.objects.count(), 2)

        Vat = _migrate_to(VAT_BEFORE).get_model("vat", "Vat")
        Vat.objects.create(value="24.0")
        self.assertEqual(Vat.objects.filter(value="24.0").count(), 2)
