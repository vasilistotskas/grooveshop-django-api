"""``0076_product_description_not_null`` backfills, then constrains.

Drives the real ``MigrationExecutor`` across the migration in both
directions (the harness of ``test_migration_amount_due_contract``).
PostgreSQL DDL is transactional, so the ``TestCase`` transaction undoes the
schema changes and leaves the worker's database at the latest migration.

The rows the migration has to rescue are the ones the release before
``0074`` wrote during its rollout: a NULL ``description``, which the column
0074 left nullable still took.
"""

from __future__ import annotations

from django.db import connection
from django.test import TestCase

from product.factories.product import ProductFactory
from tests.utils.migrations import migrate_to

BEFORE = ("product", "0075_image_dimensions")
AFTER = ("product", "0076_product_description_not_null")


def _settle_deferred_constraints():
    """Rows written just before an ALTER leave pending FK trigger events,
    which PostgreSQL refuses to ALTER around inside one transaction."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _description_nullable() -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'product_product_translation' "
            "AND column_name = 'description'"
        )
        return cursor.fetchone()[0] == "YES"


def _descriptions() -> list[str | None]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT description FROM product_product_translation")
        return [row[0] for row in cursor]


class TestDescriptionNotNullContract(TestCase):
    @classmethod
    def setUpTestData(cls):
        ProductFactory()
        _settle_deferred_constraints()
        migrate_to(BEFORE)

    def test_a_null_the_previous_release_wrote_becomes_empty(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE product_product_translation SET description = NULL"
            )
        assert set(_descriptions()) == {None}
        _settle_deferred_constraints()

        migrate_to(AFTER)

        assert set(_descriptions()) == {""}
        assert not _description_nullable()

    def test_the_reverse_leaves_the_column_nullable_again(self):
        _settle_deferred_constraints()
        migrate_to(AFTER)
        assert not _description_nullable()

        migrate_to(BEFORE)

        assert _description_nullable()
