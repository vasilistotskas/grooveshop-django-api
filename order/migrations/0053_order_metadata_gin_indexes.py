"""Give ``order_order.metadata`` the GIN index its model always declared.

``MetaDataModel`` declares a GIN index on each of its two jsonb columns,
but ``Order.Meta`` defines its own ``indexes``, and defining it REPLACES
the abstract parents' list rather than extending it. ``Order`` splatted
``TimeStampMixinModel``'s list back in and not ``MetaDataModel``'s, so
the index was silently absent — ``Product``, which splats both, has had
it all along.

The cost fell on the Viva webhook. Every delivery resolves the order with
``metadata__contains={"viva_order_codes": [code]}`` (jsonb ``@>``), all of
1796/1797/1798 take that path, and Viva retries an unacknowledged event
hourly up to 23 times. Without the index each of those is a sequential
scan of the whole orders table, as are ``metadata__has_key`` in the B2B
admin filter and the amount-mismatch sweep in ``order/tasks.py``.

Only ``metadata`` is indexed. ``private_metadata`` has no reader on this
model, and a GIN index costs a pending-list write on every INSERT and
UPDATE whether or not anything reads it.

Why this is hand-written SQL rather than ``AddIndexConcurrently``
=================================================================

``AddIndexConcurrently`` refuses to run when ``connection.in_atomic_block``
is true, and that flag is NOT controlled by this migration's ``atomic``
attribute — it reports the transaction of whoever called us. Two callers
have one open:

* ``Tenant.save()`` with ``auto_create_schema = True`` runs
  ``migrate_schemas`` INLINE, so the platform admin's "Add tenant" form
  (which the admin wraps in ``transaction.atomic()``) replays this
  migration inside that transaction.
* ``tests_mt/conftest.py`` provisions its tenant through
  ``get_or_create``, which wraps the create in ``transaction.atomic()``.

Verified: with ``AddIndexConcurrently`` and a fresh database, the entire
MT lane fails with ``NotSupportedError``, and creating a store through
the admin would 500 and roll the tenant back.

So the build adapts to its caller, which is also exactly what each caller
wants:

* **Inside a transaction** — tenant provisioning. The schema is brand
  new and ``order_order`` is empty, so a plain ``CREATE INDEX`` is
  instant and its lock is uncontended.
* **Outside one** — the Argo CD PreSync ``migrate_schemas`` job, running
  against existing tenants whose tables hold real orders, while the old
  pods are still serving. There ``CONCURRENTLY`` is worth its two table
  scans: a plain build takes a SHARE lock on ``order_order``, blocking
  every INSERT and UPDATE — i.e. checkout — for its duration.

``statement_timeout`` is lifted for the build. The connection carries
``-c statement_timeout=30000`` (settings.py), a concurrent build is one
statement that also waits out every open snapshot on the table, and being
cancelled at 30s is what LEAVES an invalid index behind.

Resumable by construction: ``IF NOT EXISTS`` makes a re-run a no-op, and
because ``IF NOT EXISTS`` is happy to skip an index that exists but is
INVALID (the residue of a cancelled concurrent build, which Postgres does
not repair and no query will use), any such leftover is dropped first.
"""

from django.db import migrations
from django.db.migrations.operations.special import SeparateDatabaseAndState
from django.contrib.postgres.indexes import GinIndex

INDEX_NAME = "order_meta_ix"
TABLE = "order_order"
COLUMN = "metadata"


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


def _build(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return

    concurrently = "" if connection.in_atomic_block else "CONCURRENTLY"

    with connection.cursor() as cursor:
        cursor.execute("SHOW statement_timeout")
        previous = cursor.fetchone()[0]
        cursor.execute("SET statement_timeout = 0")
        try:
            _drop_if_invalid(cursor, INDEX_NAME)
            cursor.execute(
                f'CREATE INDEX {concurrently} IF NOT EXISTS "{INDEX_NAME}" '
                f'ON "{TABLE}" USING gin ("{COLUMN}")'
            )
        finally:
            cursor.execute("SET statement_timeout = %s", [previous])


def _drop(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return

    concurrently = "" if connection.in_atomic_block else "CONCURRENTLY"

    with connection.cursor() as cursor:
        cursor.execute("SHOW statement_timeout")
        previous = cursor.fetchone()[0]
        cursor.execute("SET statement_timeout = 0")
        try:
            cursor.execute(
                f'DROP INDEX {concurrently} IF EXISTS "{INDEX_NAME}"'
            )
        finally:
            cursor.execute("SET statement_timeout = %s", [previous])


class Migration(migrations.Migration):
    # Lets the PreSync job build CONCURRENTLY. It does NOT make the
    # tenant-provisioning callers non-transactional — see the docstring.
    atomic = False

    dependencies = [
        ("order", "0052_fold_viva_order_code_into_history"),
    ]

    operations = [
        SeparateDatabaseAndState(
            # The database half is hand-written; the state half keeps
            # Django's model state in step so `makemigrations --check`
            # stays clean.
            database_operations=[
                migrations.RunPython(_build, _drop, atomic=False),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="order",
                    index=GinIndex(fields=[COLUMN], name=INDEX_NAME),
                ),
            ],
        ),
    ]
