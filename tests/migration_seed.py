"""Keep every test database holding exactly the rows its migrations seed.

Data migrations seed rows the code relies on — countries and regions,
shipping providers and their rates, pay ways, VAT rates, loyalty tiers
and ``django-extra-settings``' defaults. Those rows are
part of each xdist worker's freshly migrated test database, but a
transactional test (``django_db(transaction=True)`` or a Django
``TransactionTestCase``) ends with ``flush``, which truncates every
table. Django's own runner hides that by running transactional tests
last (``pytest-django`` reorders the same way), but under
``--dist loadfile`` a file is dispatched whole, so a file that mixes
both kinds flushes its worker mid-session and every later test on that
worker ran against an empty seed.

The suite used to answer that with per-test fixtures that re-created a
hand-copied subset of the seed before EVERY database test. Those writes
ran inside the test's own rolled-back transaction, so once a worker had
been flushed, every later test re-seeded ~270 rows only to discard
them: measured on ``tests/integration/order``, 0.8 s of the average
test's setup, over half the lane's worker time. Pay ways, VAT rates and
loyalty tiers were never restored at all, and the
hand copy of the shipping seed had drifted from its migration.

This module restores the seed where it is lost instead, once, committed:

* when ``migrate`` has built a test database (a non-empty ``plan``),
  every non-empty table is copied into the ``SNAPSHOT_SCHEMA`` schema.
  On a reused database that already holds a snapshot, ``pre_migrate``
  first restores it, so rows an interrupted run committed are not
  copied into the new one;
* when ``flush`` has emptied one, the snapshot is copied back;
* when ``migrate`` had nothing to apply — ``--reuse-db`` found the
  database already built — every table is emptied and the snapshot
  copied back, so a session starts from the migrated state even if the
  previous one was interrupted mid-test.

``flush`` announces itself through ``post_migrate`` with no ``plan``
(``django.core.management.sql.emit_post_migrate_signal``), after the
other receivers have re-created content types, permissions, the default
``Site`` and the ``extra_settings`` defaults with new primary keys; the
restore replaces those rows with the migrated ones, so every primary key
a migration created is the same in every test.

Three ``TransactionTestCase`` options bypass this, and the suite refuses
them at collection (``tests/conftest.py``): ``available_apps`` and
``serialized_rollback`` make ``flush`` skip ``post_migrate``, so nothing
is restored, and ``reset_sequences`` restarts the sequences under the
primary keys the restore puts back.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import connections, transaction
from django.db.models.signals import post_migrate, pre_migrate
from django_tenants.utils import get_public_schema_name

SNAPSHOT_SCHEMA = "test_migration_seed"


def _quote(connection, name: str) -> str:
    return connection.ops.quote_name(name)


def _django_tables(connection) -> list[str]:
    # The same set ``flush`` truncates (``django.core.management.sql``).
    return connection.introspection.django_table_names(
        only_existing=True, include_views=False
    )


def _snapshot_tables(connection) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = %s",
            [SNAPSHOT_SCHEMA],
        )
        return [row[0] for row in cursor.fetchall()]


def take_snapshot(connection) -> None:
    """Copy every table that holds rows into ``SNAPSHOT_SCHEMA``."""
    schema = _quote(connection, SNAPSHOT_SCHEMA)
    with transaction.atomic(using=connection.alias):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            cursor.execute(f"CREATE SCHEMA {schema}")
            for table in _django_tables(connection):
                quoted = _quote(connection, table)
                cursor.execute(f"SELECT EXISTS (SELECT 1 FROM {quoted})")
                if cursor.fetchone()[0]:
                    cursor.execute(
                        f"CREATE TABLE {schema}.{quoted} AS TABLE {quoted}"
                    )


def restore_snapshot(connection, *, empty_every_table: bool) -> None:
    """Replace the seeded tables' contents with the snapshot.

    ``empty_every_table`` also empties the tables the snapshot does not
    hold — needed at session start, where a reused database may still
    carry rows an interrupted run committed; after ``flush`` they are
    already empty.
    """
    seeded = _snapshot_tables(connection)
    if not seeded:
        raise RuntimeError(
            f"The test database has no {SNAPSHOT_SCHEMA!r} schema: it was "
            "built before tests/migration_seed.py existed. Rebuild it "
            "once with `pytest --create-db`."
        )
    to_empty = _django_tables(connection) if empty_every_table else seeded
    schema = _quote(connection, SNAPSHOT_SCHEMA)
    with transaction.atomic(using=connection.alias):
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE "
                + ", ".join(_quote(connection, t) for t in to_empty)
                + " CASCADE"
            )
            # Foreign keys are DEFERRABLE INITIALLY DEFERRED, so the
            # insertion order does not matter inside one transaction.
            for table in seeded:
                quoted = _quote(connection, table)
                cursor.execute(
                    f"INSERT INTO {quoted} SELECT * FROM {schema}.{quoted}"
                )
    # The receivers that ran before this one cached the content types
    # they re-created, under primary keys the restore just replaced.
    ContentType.objects.clear_cache()


def _on_post_migrate(sender, using, plan=None, **kwargs) -> None:
    connection = connections[using]
    # django-tenants' ``migrate`` goes on to migrate every tenant schema
    # (the seeded ``webside`` one included) with the search_path on it,
    # and sends this signal there too. The snapshot is only ever of — and
    # restored into — the schema this suite runs in.
    if connection.schema_name != get_public_schema_name():
        return
    if plan:
        take_snapshot(connection)
    else:
        restore_snapshot(connection, empty_every_table=plan is not None)


def _on_pre_migrate(sender, using, plan=None, **kwargs) -> None:
    connection = connections[using]
    if connection.schema_name != get_public_schema_name() or not plan:
        return
    # New migrations are about to run on a reused database: put it back
    # in its migrated state first, or the snapshot taken after them would
    # capture whatever an interrupted run left committed. A database
    # built from nothing has no snapshot yet and nothing to clean.
    if _snapshot_tables(connection):
        restore_snapshot(connection, empty_every_table=True)


def connect() -> None:
    """Run after every other ``post_migrate`` receiver.

    ``post_migrate`` is sent once per app, in ``INSTALLED_APPS`` order;
    receiving it for the last app only, and connecting after every
    ``AppConfig.ready()`` has connected its own, puts this receiver
    last.
    """
    with_models = [
        config
        for config in apps.get_app_configs()
        if config.models_module is not None
    ]
    # ``pre_migrate`` is sent for every app before any migration runs,
    # so the first app is as good as any.
    pre_migrate.connect(
        _on_pre_migrate, sender=with_models[0], dispatch_uid=__name__
    )
    post_migrate.connect(
        _on_post_migrate, sender=with_models[-1], dispatch_uid=__name__
    )
