"""``0029_payway_key`` carries each pay way's translated name onto the row.

Drives the real ``MigrationExecutor`` across the migration, in both
directions, with rows written through the historical models (the same
harness idea as
``tests/integration/core/test_migration_seo_into_translations.py``).
PostgreSQL DDL is transactional, so the ``TestCase`` transaction undoes
the schema changes and leaves the worker's database at the latest
migration again.
"""

from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

BEFORE = ("pay_way", "0028_field_verbose_names")
AFTER = ("pay_way", "0029_payway_key")


def _settle_deferred_constraints():
    """Rows written just before an ALTER leave pending FK trigger events,
    which PostgreSQL refuses to ALTER around inside one transaction."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _migrate_to(target):
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


class TestKeyMigration(TestCase):
    def _before(self):
        apps = _migrate_to(BEFORE)
        return (
            apps.get_model("pay_way", "PayWay"),
            apps.get_model("pay_way", "PayWayTranslation"),
        )

    def _pay_way_with_names(self, code: str, **names: str):
        PayWay, Translation = self._before()
        pay_way = PayWay.objects.create(provider_code=code)
        for language, name in names.items():
            Translation.objects.create(
                master=pay_way, language_code=language, name=name
            )
        _settle_deferred_constraints()
        return pay_way.pk

    def _after_key(self, pk: int) -> str:
        apps = _migrate_to(AFTER)
        return apps.get_model("pay_way", "PayWay").objects.get(pk=pk).key

    def test_the_el_name_becomes_the_key(self):
        pk = self._pay_way_with_names(
            "m_el", el="PAY_ON_DELIVERY", en="CREDIT_CARD"
        )

        self.assertEqual(self._after_key(pk), "PAY_ON_DELIVERY")

    def test_another_language_is_used_when_el_has_none(self):
        pk = self._pay_way_with_names("m_en", en="STRIPE")

        self.assertEqual(self._after_key(pk), "STRIPE")

    def test_a_row_with_no_name_keeps_an_empty_key(self):
        pk = self._pay_way_with_names("m_none")

        self.assertEqual(self._after_key(pk), "")

    def test_the_translated_name_column_is_gone(self):
        apps = _migrate_to(AFTER)
        fields = {
            f.name
            for f in apps.get_model("pay_way", "PayWayTranslation")._meta.fields
        }

        self.assertNotIn("name", fields)

    def test_reverse_writes_the_key_back_to_el(self):
        apps = _migrate_to(AFTER)
        PayWay = apps.get_model("pay_way", "PayWay")
        pk = PayWay.objects.create(
            provider_code="m_rev", key="BANK_TRANSFER"
        ).pk
        _settle_deferred_constraints()

        PayWay, Translation = self._before()

        self.assertEqual(
            Translation.objects.get(master_id=pk, language_code="el").name,
            "BANK_TRANSFER",
        )
