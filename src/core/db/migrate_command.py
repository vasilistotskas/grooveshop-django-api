"""The ``migrate`` command every ``migrate_schemas`` run uses.

django-tenants runs it for each schema (``TENANT_BASE_MIGRATE_COMMAND``).
When there is no outer transaction, each migration commits on its own, and
PostgreSQL checks the foreign keys a data migration wrote (they are
``DEFERRABLE INITIALLY DEFERRED``) at that commit. Provisioning a store
is different: ``Tenant.save()`` with ``auto_create_schema`` replays the
whole history into the new schema inside the caller's transaction
(``tenant_create``, the platform admin's "Add tenant"), so every
migration is a savepoint and those checks stay queued until the end.
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


class Command(migrate.Command):
    def handle(self, *args: Any, **options: Any) -> Any:
        self.migrating_connection = connections[options["database"]]
        return super().handle(*args, **options)

    def migration_progress_callback(
        self, action: str, migration: Any = None, fake: bool = False
    ) -> None:
        super().migration_progress_callback(action, migration, fake)
        if (
            action in {"apply_success", "unapply_success"}
            and not fake
            and self.migrating_connection.in_atomic_block
        ):
            # ``SET CONSTRAINTS ALL IMMEDIATE`` then ``ALL DEFERRED``:
            # the queued checks run now, and fail here if the data is wrong.
            self.migrating_connection.check_constraints()
