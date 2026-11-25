"""The ``migrate`` command every ``migrate_schemas`` run uses.

django-tenants runs it for each schema (``TENANT_BASE_MIGRATE_COMMAND``).
When there is no outer transaction, each migration commits on its own, and
PostgreSQL checks the foreign keys a data migration wrote (they are
``DEFERRABLE INITIALLY DEFERRED``) at that commit. Provisioning a store
is different: ``Tenant.save()`` with ``auto_create_schema`` replays the
whole history into the new schema inside the caller's transaction
(``tenant_create``, the platform admin's "Add tenant"), so no migration
commits (an atomic one runs in a savepoint, an ``atomic = False`` one
straight in that transaction) and those checks stay queued until the end.
PostgreSQL refuses to ``ALTER`` or ``DROP`` a table that still has
queued trigger events, so a later migration that alters a table a data
migration filled fails ("cannot ALTER TABLE ... because it has pending
trigger events"), or not, depending on whether some unrelated migration
between them happened to run ``SET CONSTRAINTS ALL IMMEDIATE`` first.

After each migration this command runs those checks, as the commit would
have, and puts the constraints back to deferred, so every migration starts
as it would in a transaction of its own, whatever the graph's order.
"""

from __future__ import annotations

from typing import Any

from django.core.management.commands import migrate
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper


def settle_deferred_constraints(
    connection: BaseDatabaseWrapper, action: str, *, fake: bool
) -> None:
    """Run the foreign-key checks a migration queued, as its commit would
    have, when it ran inside an outer transaction.

    A ``MigrationExecutor`` progress callback's body: this command calls
    it after each migration, and so does every other driver of the
    executor that migrates inside a transaction (the migration tests'
    ``tests/utils/migrations.py``).
    """
    if (
        action in {"apply_success", "unapply_success"}
        and not fake
        and connection.in_atomic_block
    ):
        # ``SET CONSTRAINTS ALL IMMEDIATE`` then ``ALL DEFERRED``:
        # the queued checks run now, and fail here if the data is wrong.
        connection.check_constraints()


class Command(migrate.Command):
    def handle(self, *args: Any, **options: Any) -> Any:
        self.migrating_connection = connections[options["database"]]
        return super().handle(*args, **options)

    def migration_progress_callback(
        self, action: str, migration: Any = None, fake: bool = False
    ) -> None:
        super().migration_progress_callback(action, migration, fake)
        settle_deferred_constraints(
            self.migrating_connection, action, fake=fake
        )
