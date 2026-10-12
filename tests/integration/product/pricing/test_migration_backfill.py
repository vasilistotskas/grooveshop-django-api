"""``0050_backfill_price_spells`` through the real ``MigrationExecutor``.

Rows are written with the historical models of ``0048`` (before spells
existed), the migration runs, and the result is read back with the
models of ``0050``. ``TestCase`` rolls the DDL back with the
transaction, leaving the worker's database at the latest migration (the
harness of ``tests/integration/order/test_migration_amount_due.py``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

BEFORE = ("product", "0048_field_verbose_names")
AFTER = ("product", "0050_backfill_price_spells")

NOW = datetime.now(tz=UTC)


def _settle_deferred_constraints():
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _migrate_to(target):
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


class TestBackfillMigration(TestCase):
    def _product(self, apps, *, price, discount, vat_id, history=()):
        """A product at its CURRENT state plus the history rows it kept.

        ``history`` is ``(days_ago, price, discount)`` per row, oldest
        first, each one a snapshot ``HistoricalProduct`` would hold.
        """
        Product = apps.get_model("product", "Product")
        Historical = apps.get_model("product", "HistoricalProduct")
        product = Product.objects.create(
            sku=str(uuid.uuid4()),
            slug=str(uuid.uuid4()),
            price=price,
            price_currency="EUR",
            discount_percent=discount,
            vat_id=vat_id,
        )
        for days_ago, row_price, row_discount in history:
            Historical.objects.create(
                id=product.pk,
                uuid=product.uuid,
                sku=product.sku,
                slug=product.slug,
                price=row_price,
                price_currency="EUR",
                discount_percent=row_discount,
                vat_id=vat_id,
                created_at=NOW,
                updated_at=NOW,
                history_date=NOW - timedelta(days=days_ago),
                history_type="~",
            )
        _settle_deferred_constraints()
        return product.pk

    def _vat(self, apps, value="24.0"):
        Vat = apps.get_model("vat", "Vat")
        return Vat.objects.get_or_create(value=Decimal(value))[0].pk

    def _run(self, **kwargs):
        before = _migrate_to(BEFORE)
        vat_id = self._vat(before)
        pk = self._product(before, vat_id=vat_id, **kwargs)
        after = _migrate_to(AFTER)
        Spell = after.get_model("product", "ProductPriceSpell")
        Product = after.get_model("product", "Product")
        return (
            list(Spell.objects.filter(product_id=pk).order_by("valid_from")),
            Product.objects.get(pk=pk),
        )

    def test_a_markdown_that_started_last_week_has_its_reference(self):
        spells, product = self._run(
            price=Decimal("100.00"),
            discount=Decimal(20),
            history=[
                (200, Decimal("100.00"), Decimal(0)),
                (7, Decimal("100.00"), Decimal(20)),
            ],
        )

        self.assertEqual(
            [spell.price.amount for spell in spells],
            [Decimal("124.00"), Decimal("104.00")],
        )
        self.assertEqual(spells[0].valid_to, spells[1].valid_from)
        self.assertIsNone(spells[1].valid_to)
        self.assertEqual(product.effective_price, Decimal("104.00"))
        self.assertEqual(product.prior_price, Decimal("124.00"))
        self.assertEqual(product.reduction_started_at, spells[1].valid_from)

    def test_a_product_with_no_history_gets_one_spell(self):
        spells, product = self._run(price=Decimal("50.00"), discount=Decimal(0))

        self.assertEqual(
            [spell.price.amount for spell in spells], [Decimal("62.00")]
        )
        self.assertEqual(product.effective_price, Decimal("62.00"))
        self.assertIsNone(product.prior_price)
        self.assertIsNone(product.reduction_started_at)

    def test_a_markdown_on_a_young_product_announces_nothing(self):
        spells, product = self._run(
            price=Decimal("100.00"),
            discount=Decimal(20),
            history=[
                (10, Decimal("100.00"), Decimal(0)),
                (2, Decimal("100.00"), Decimal(20)),
            ],
        )

        self.assertEqual(len(spells), 2)
        self.assertIsNone(product.prior_price)
        self.assertIsNotNone(product.reduction_started_at)

    def test_running_it_twice_adds_nothing(self):
        before = _migrate_to(BEFORE)
        vat_id = self._vat(before)
        pk = self._product(
            before,
            price=Decimal("100.00"),
            discount=Decimal(0),
            vat_id=vat_id,
        )
        after = _migrate_to(AFTER)
        from importlib import import_module

        module = import_module("product.migrations.0050_backfill_price_spells")

        module.backfill_price_spells(after, None)

        Spell = after.get_model("product", "ProductPriceSpell")
        self.assertEqual(Spell.objects.filter(product_id=pk).count(), 1)

    def test_the_old_columns_are_untouched_and_it_reverses(self):
        before = _migrate_to(BEFORE)
        vat_id = self._vat(before)
        pk = self._product(
            before,
            price=Decimal("100.00"),
            discount=Decimal(0),
            vat_id=vat_id,
        )
        _migrate_to(AFTER)

        back = _migrate_to(BEFORE)

        Product = back.get_model("product", "Product")
        self.assertEqual(
            Product.objects.get(pk=pk).price.amount, Decimal("100.00")
        )
