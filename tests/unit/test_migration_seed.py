"""``tests/migration_seed.py``: every test starts from the migrated seed.

The whole suite leans on it implicitly; these pin the three moments it
acts — a ``flush``, a reused database, new migrations on a reused one —
and the options it cannot serve, which ``tests/conftest.py`` refuses.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.db import connection
from django.test import TransactionTestCase

from shipping.models import ShippingRate
from tag.models import Tag
from tests import migration_seed
from tests.conftest import _seed_bypassing_options


def _snapshot_count(table: str) -> int:
    schema = connection.ops.quote_name(migration_seed.SNAPSHOT_SCHEMA)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT count(*) FROM {schema}.{connection.ops.quote_name(table)}"
        )
        return cursor.fetchone()[0]


@pytest.mark.django_db
class TestRestore:
    def test_a_restore_puts_back_exactly_the_migrated_rows(self):
        seeded = _snapshot_count(ShippingRate._meta.db_table)
        assert seeded > 0, "sanity: shipping rates are seeded by a migration"
        ShippingRate.objects.all().delete()
        Tag.objects.create(active=True)

        migration_seed.restore_snapshot(connection, empty_every_table=True)

        assert ShippingRate.objects.count() == seeded
        assert not Tag.objects.exists()

    def test_new_migrations_on_a_reused_database_start_from_the_snapshot(
        self,
    ):
        """What an interrupted run committed must not be copied into the
        snapshot the new migrations' ``post_migrate`` then takes."""
        Tag.objects.create(active=True)

        migration_seed._on_pre_migrate(
            sender=None, using=connection.alias, plan=[(object(), False)]
        )

        assert not Tag.objects.exists()

    @pytest.mark.parametrize("plan", [None, []])
    def test_nothing_happens_before_an_empty_plan(self, plan):
        Tag.objects.create(active=True)

        migration_seed._on_pre_migrate(
            sender=None, using=connection.alias, plan=plan
        )

        assert Tag.objects.count() == 1


class TestAFlushRestoresTheSeed(TransactionTestCase):
    """Runs in definition order: the first test commits the loss, its
    teardown ``flush`` triggers the restore, the second sees the seed."""

    def test_1_losing_the_seed_commits(self):
        ShippingRate.objects.all().delete()

        assert not ShippingRate.objects.exists()

    def test_2_the_next_test_has_it_back(self):
        assert ShippingRate.objects.count() == _snapshot_count(
            ShippingRate._meta.db_table
        )


def _item(*, marker=None, cls=None, fixturenames=()):
    return SimpleNamespace(
        get_closest_marker=lambda name: marker,
        cls=cls,
        fixturenames=list(fixturenames),
    )


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (_item(marker=pytest.mark.django_db.mark), []),
        (_item(marker=pytest.mark.django_db(transaction=True).mark), []),
        (
            _item(marker=pytest.mark.django_db(reset_sequences=True).mark),
            ["reset_sequences"],
        ),
        (
            _item(
                marker=pytest.mark.django_db(
                    transaction=True, serialized_rollback=True
                ).mark
            ),
            ["serialized_rollback"],
        ),
        (
            _item(
                marker=pytest.mark.django_db(
                    transaction=True, available_apps=["tag"]
                ).mark
            ),
            ["available_apps"],
        ),
        (
            _item(
                marker=pytest.mark.django_db.mark,
                fixturenames=["django_db_reset_sequences"],
            ),
            ["django_db_reset_sequences"],
        ),
        (
            _item(
                cls=type(
                    "Serialized",
                    (TransactionTestCase,),
                    {"serialized_rollback": True},
                )
            ),
            ["serialized_rollback"],
        ),
        (_item(cls=TransactionTestCase), []),
        (_item(), []),
    ],
)
def test_the_options_the_restore_cannot_serve_are_named(item, expected):
    assert _seed_bypassing_options(item) == expected
