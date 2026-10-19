"""Migration operations shared by the apps that name a staff member.

See ``tenant.staff_actors`` for why such a column holds a plain id.
"""

from __future__ import annotations

from django.db import migrations
from django_tenants.utils import get_public_schema_name

# Django names an FK constraint with a hash suffix: look it up.
_FIND_CONSTRAINT = """
    SELECT con.conname
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE rel.relname = %s
      AND con.contype = 'f'
      AND nsp.nspname = %s
      AND pg_get_constraintdef(con.oid) LIKE %s
"""


def drop_actor_fk(table: str, column: str) -> migrations.RunPython:
    """Drop the FK from ``table.column`` to the store's user table.

    A staff member is a public-schema identity, so the store's user table
    cannot hold their id: with the constraint the save fails for a staff
    member the store has no copy of, and where it passes it only passes
    because a shopper has that id. Only store schemas have the constraint
    to drop, and the data (a plain id) is untouched. Idempotent; the
    reverse does nothing, because re-adding it would fail on exactly the
    rows this exists for.
    """

    def forwards(apps, schema_editor):
        connection = schema_editor.connection
        schema = getattr(connection, "schema_name", None)
        if schema is None or schema == get_public_schema_name():
            return
        with connection.cursor() as cursor:
            cursor.execute(
                _FIND_CONSTRAINT,
                [table, schema, f"FOREIGN KEY ({column})%"],
            )
            row = cursor.fetchone()
            if row is not None:
                cursor.execute(
                    f'ALTER TABLE "{schema}"."{table}" '
                    f'DROP CONSTRAINT "{row[0]}"'
                )

    return migrations.RunPython(
        forwards, migrations.RunPython.noop, elidable=False
    )
