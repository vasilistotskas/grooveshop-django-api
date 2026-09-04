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

``AddIndexAdaptively`` was extracted from
``order/migrations/0053_order_metadata_gin_indexes``, where it was written
and verified against real tenant schemas first.
"""

from __future__ import annotations

import logging

from django.db.migrations.operations.base import Operation
from django.db.migrations.operations.models import AddIndex, RemoveIndex

logger = logging.getLogger(__name__)


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
            RemoveIndex(self.model_name, self.index.name).database_backwards(
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


class BackfillNullStringsToEmpty(Operation):
    """Rewrite NULL to ``''`` on string columns, in bounded batches.

    A string column that allows NULL has two spellings for "no value"
    (Django's own field docs say to avoid it, and ruff's ``DJ001`` flags
    it). Removing the NULL spelling is a **two-release** change under the
    Argo CD PreSync hook, because migrations land before the new image:
    the ALTER would run while pods that still write NULL are serving. So
    the first release stops producing NULL and backfills what is there —
    this operation — and only the second may add the constraint.

    **State-free on purpose.** It changes no field, so it emits no
    ``AlterField`` into the migration state; the model still declares
    ``null=True`` at this point, and that is exactly right.

    Written as a pk-range walk rather than the usual
    ``WHERE ctid IN (SELECT … LIMIT n)`` loop. That idiom re-scans from
    the top on every batch, traversing every row it already fixed, which
    is quadratic on the one table here that actually grows (search
    analytics). Walking the primary key uses its index, touches each row
    once, and bounds how many rows any single statement locks — which is
    what matters when the store is still serving on the old pods.

    ``COALESCE`` per column, so a row that is NULL in one column and
    already populated in another keeps the populated one.

    The migration using this MUST set ``atomic = False``: one transaction
    around every batch would hold every row lock to the end and defeat
    the batching. Tenant PROVISIONING is the exception that proves it —
    ``Tenant.save()`` replays the history inline inside a transaction, so
    the batching degenerates to one transaction there. Harmless, because
    the schema is new and the walk returns on the first ``MIN(pk)``.

    Backwards is a deliberate **no-op** — unapplyable rather than truly
    reversible. A column holding ``''`` cannot say which of those were
    NULL before, and nothing needs it to: the code being rolled back to
    treats ``''`` and NULL alike, since every reader tests these columns
    for truth. ``reversible = True`` so ``migrate <app> zero`` still runs.

    **Not sufficient on its own as the final sweep before ``SET NOT
    NULL``.** ``MIN``/``MAX`` are read once, so a row whose pk falls
    below ``MAX`` but whose INSERT commits after the batch covering it
    has passed is never visited — two writers drawing pks 100 and 101
    where 101 commits first. Under READ COMMITTED no lock closes that.
    The release that adds the constraint must therefore stop the writers
    BEFORE it sweeps:

        1. ``ADD CONSTRAINT … CHECK (col IS NOT NULL) NOT VALID`` —
           instant, no scan, and from here no writer can insert a NULL;
        2. this operation, now racing nobody;
        3. ``VALIDATE CONSTRAINT`` — scans without blocking writes;
        4. ``SET NOT NULL``, whose own scan the valid CHECK lets Postgres
           skip;
        5. drop the CHECK.
    """

    reduces_to_sql = False
    reversible = True

    # A batch that waits on a row lock is worse than a batch that fails:
    # the connection carries `statement_timeout=30000`, so a collision
    # with a live UPDATE would block a customer's request for the full
    # thirty seconds. Failing fast instead surfaces the collision, and
    # the PreSync Job's own retry resumes — the walk is idempotent.
    lock_timeout = "3s"

    def __init__(
        self,
        model_name: str,
        fields: list[str],
        *,
        batch_size: int = 5000,
    ):
        if batch_size < 1:
            # A zero step never advances `start`: an infinite loop inside
            # the PreSync hook, ended only by `activeDeadlineSeconds`.
            raise ValueError(f"batch_size must be >= 1, got {batch_size!r}")
        self.model_name = model_name
        self.fields = fields
        self.batch_size = batch_size

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        connection = schema_editor.connection
        model = to_state.apps.get_model(app_label, self.model_name)
        schema = getattr(connection, "schema_name", "public")
        target = f"{schema}.{model._meta.db_table}"
        # Raw SQL bypasses the router; Django's own operations make this
        # check for us, so doing it by hand is the price of raw SQL.
        if not self.allow_migrate_model(connection.alias, model):
            logger.info(
                "BackfillNullStringsToEmpty: %s — router declined, skipped",
                target,
            )
            return

        pk = model._meta.pk.column
        # `search_path` is `"<schema>", public` while a tenant migrates,
        # so an UNQUALIFIED name silently falls through to the public
        # schema's copy of the table when a tenant lacks its own —
        # rewriting public's rows once per tenant. Qualify it.
        table = f'"{schema}"."{model._meta.db_table}"'
        columns = [model._meta.get_field(f).column for f in self.fields]
        assignments = ", ".join(
            f'"{c}" = COALESCE("{c}", \'\')' for c in columns
        )
        predicate = " OR ".join(f'"{c}" IS NULL' for c in columns)

        with connection.cursor() as cursor:
            cursor.execute(f'SELECT MIN("{pk}"), MAX("{pk}") FROM {table}')
            low, high = cursor.fetchone()
            if low is None:
                logger.info(
                    "BackfillNullStringsToEmpty: %s — empty table, nothing "
                    "to rewrite",
                    target,
                )
                return
            if not isinstance(low, int):
                # The walk steps by adding an integer to the pk.
                raise TypeError(
                    f"{target} has a non-integer primary key "
                    f"({type(low).__name__}); the pk-range walk cannot "
                    f"step over it."
                )

            cursor.execute("SHOW lock_timeout")
            previous_lock_timeout = cursor.fetchone()[0]
            cursor.execute("SET lock_timeout = %s", [self.lock_timeout])
            try:
                updated = 0
                start = low
                while start <= high:
                    stop = start + self.batch_size
                    cursor.execute(
                        f"UPDATE {table} SET {assignments} "
                        f'WHERE "{pk}" >= %s AND "{pk}" < %s '
                        f"AND ({predicate})",
                        [start, stop],
                    )
                    updated += cursor.rowcount
                    start = stop
            finally:
                cursor.execute("SET lock_timeout = %s", [previous_lock_timeout])

        logger.info(
            "BackfillNullStringsToEmpty: %s (%s) — %s rows rewritten across "
            "pk %s..%s",
            target,
            ", ".join(self.fields),
            updated,
            low,
            high,
        )

    def database_backwards(
        self, app_label, schema_editor, from_state, to_state
    ):
        return

    def describe(self):
        return (
            f"Backfill NULL to '' on {self.model_name}.{', '.join(self.fields)}"
        )

    @property
    def migration_name_fragment(self):
        return f"backfill_{self.model_name.lower()}_empty_strings"
