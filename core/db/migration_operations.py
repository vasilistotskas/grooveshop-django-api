"""Migration operations shared across apps.

**This module is imported by migrations, so it is append-only in spirit.**
Historical migrations must keep working forever, which means nothing here
may change meaning or be deleted — only added to. That is the same
reasoning behind the rule elsewhere in this repo that a migration should
not import app code (see ``shipping/migrations/0004_seed_provider_metadata``):
app code moves, and a migration that imported it breaks on replay. An
operation is the one thing that has to be shared rather than copied,
because duplicating the logic below into every migration that needs it is
how the copies drift apart.

Extracted from ``order/migrations/0053_order_metadata_gin_indexes``, where
this was written and verified against real tenant schemas first.
"""

from __future__ import annotations

from django.db.migrations.operations.base import Operation
from django.db.migrations.operations.models import AddIndex


class AddIndexAdaptively(Operation):
    """Add an index, choosing CONCURRENTLY based on the caller's context.

    ``AddIndexConcurrently`` cannot be used in this project. It refuses to
    run when ``connection.in_atomic_block`` is true, and that flag
    reports the CALLER's transaction, not the migration's own ``atomic``
    attribute. ``Tenant.save()`` has ``auto_create_schema = True`` and
    replays the whole migration history INLINE, so the platform admin's
    add form (which Django wraps in ``transaction.atomic``) and
    ``tests_mt``'s ``get_or_create`` both run migrations inside a
    transaction and would hard-fail.

    So the build adapts, which is also what each caller actually wants:

    * **inside a transaction** — tenant provisioning against a brand-new,
      empty schema, where a plain ``CREATE INDEX`` is instant and its
      lock uncontended;
    * **outside one** — the Argo CD PreSync ``migrate_schemas`` job,
      running against tenants that hold real rows while the OLD pods are
      still serving. There ``CONCURRENTLY`` earns its two table scans,
      because a plain build takes a SHARE lock that blocks every INSERT
      and UPDATE for its duration.

    ``statement_timeout`` is lifted for the build: the connection carries
    ``-c statement_timeout=30000``, a concurrent build is a single
    statement that also waits out every open snapshot on the table, and
    being cancelled is precisely what leaves an INVALID index behind.

    Resumable by construction — ``IF NOT EXISTS`` makes a re-run a no-op,
    and because ``IF NOT EXISTS`` happily skips an index that exists but
    is INVALID (the residue of a cancelled concurrent build, which
    Postgres never repairs and no query will use), any such leftover is
    dropped first.

    The migration using this MUST set ``atomic = False``, or the
    non-transactional branch can never be taken.
    """

    reduces_to_sql = False
    reversible = True

    def __init__(self, model_name: str, index):
        self.model_name = model_name
        self.index = index

    # -- state -------------------------------------------------------
    def state_forwards(self, app_label, state):
        AddIndex(self.model_name, self.index).state_forwards(app_label, state)

    # -- database ----------------------------------------------------
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        connection = schema_editor.connection
        model = to_state.apps.get_model(app_label, self.model_name)
        # Raw SQL bypasses the router, so the check Django's own
        # operations do has to be made explicitly. Without it this tried
        # to index a TENANT app's table while migrating the public
        # schema, where that table does not exist.
        if not self.allow_migrate_model(connection.alias, model):
            return

        if connection.vendor != "postgresql":
            AddIndex(self.model_name, self.index).database_forwards(
                app_label, schema_editor, from_state, to_state
            )
            return

        table = model._meta.db_table
        name = self.index.name
        columns = ", ".join(f'"{f.lstrip("-")}"' for f in self.index.fields)
        concurrently = "" if connection.in_atomic_block else "CONCURRENTLY"

        with connection.cursor() as cursor:
            previous = _lift_statement_timeout(cursor)
            try:
                _drop_if_invalid(cursor, name)
                cursor.execute(
                    f'CREATE INDEX {concurrently} IF NOT EXISTS "{name}" '
                    f'ON "{table}" ({columns})'
                )
            finally:
                _restore_statement_timeout(cursor, previous)

    def database_backwards(
        self, app_label, schema_editor, from_state, to_state
    ):
        connection = schema_editor.connection
        model = from_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(connection.alias, model):
            return

        if connection.vendor != "postgresql":
            # ``AddIndex.database_backwards``, mirroring the
            # ``AddIndex.database_forwards`` delegation above. NOT
            # ``RemoveIndex.database_backwards``, which is what stood
            # here: reversing a REMOVAL means adding, so that method
            # calls ``schema_editor.add_index()`` — the reverse of this
            # operation would have created the index it is supposed to
            # drop, and failed on a duplicate name where it already
            # existed.
            AddIndex(self.model_name, self.index).database_backwards(
                app_label, schema_editor, from_state, to_state
            )
            return

        concurrently = "" if connection.in_atomic_block else "CONCURRENTLY"
        with connection.cursor() as cursor:
            previous = _lift_statement_timeout(cursor)
            try:
                cursor.execute(
                    f'DROP INDEX {concurrently} IF EXISTS "{self.index.name}"'
                )
            finally:
                _restore_statement_timeout(cursor, previous)

    # -- reporting ---------------------------------------------------
    def describe(self):
        return (
            f"Create index {self.index.name} on {self.model_name} "
            f"(concurrently when not already in a transaction)"
        )

    @property
    def migration_name_fragment(self):
        return f"{self.model_name.lower()}_{self.index.name.lower()}"


def _lift_statement_timeout(cursor) -> str:
    cursor.execute("SHOW statement_timeout")
    previous = cursor.fetchone()[0]
    cursor.execute("SET statement_timeout = 0")
    return previous


def _restore_statement_timeout(cursor, previous: str) -> None:
    cursor.execute("SET statement_timeout = %s", [previous])


def _drop_if_invalid(cursor, index_name: str) -> None:
    """Remove a leftover INVALID index so ``IF NOT EXISTS`` can rebuild.

    A cancelled ``CREATE INDEX CONCURRENTLY`` leaves the index in place
    marked ``indisvalid = false``. Postgres never finishes it, no query
    uses it, and it still costs writes — but it occupies the name.
    """
    cursor.execute(
        """
        SELECT 1
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        JOIN pg_namespace n ON n.oid = i.relnamespace
        WHERE i.relname = %s
          AND n.nspname = current_schema()
          AND NOT x.indisvalid
        """,
        [index_name],
    )
    if cursor.fetchone():
        cursor.execute(f'DROP INDEX IF EXISTS "{index_name}"')
