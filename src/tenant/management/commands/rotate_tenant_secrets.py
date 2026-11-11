"""Re-encrypt every tenant secret under the current primary key.

The last step of a key rotation (``docs/secret-encryption.md``): once the
new key leads ``FIELD_ENCRYPTION_KEYS`` everywhere, this rewrites each
stored token that an older key encrypted. Afterwards the old key can be
removed from the setting.

Idempotent: a token the primary key already opens is left alone, so a
second run rewrites nothing. The tokens are rewritten as they are, in
SQL, never through the model (which would encrypt a token again), and
only counts are printed — never a value. A token no configured key opens
aborts the run before anything is written for it, naming the tenant
and the field.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from core.db.encrypted_fields import (
    DecryptionError,
    EncryptedCharField,
    rotate_value,
)
from tenant.models import Tenant


class Command(BaseCommand):
    help = (
        "Re-encrypt every tenant secret under the primary key in "
        "FIELD_ENCRYPTION_KEYS (idempotent)."
    )

    def handle(self, *args, **options):
        table = connection.ops.quote_name(Tenant._meta.db_table)
        columns = [
            field
            for field in Tenant._meta.get_fields()
            if isinstance(field, EncryptedCharField)
        ]
        rotated = unchanged = 0
        touched: set[int] = set()
        with transaction.atomic(), connection.cursor() as cursor:
            for field in columns:
                column = connection.ops.quote_name(str(field.column))
                cursor.execute(
                    f"SELECT id, {column} FROM {table} WHERE {column} <> ''"
                )
                for pk, token in cursor.fetchall():
                    try:
                        fresh = rotate_value(token)
                    except DecryptionError as exc:
                        raise CommandError(
                            f"Tenant {pk}, field {field.name}: {exc}"
                        ) from exc
                    if fresh == token:
                        unchanged += 1
                        continue
                    cursor.execute(
                        f"UPDATE {table} SET {column} = %s WHERE id = %s",
                        [fresh, pk],
                    )
                    rotated += 1
                    touched.add(pk)
        self.stdout.write(
            f"Re-encrypted {rotated} value(s) across {len(touched)} "
            f"tenant(s); {unchanged} already under the primary key."
        )
