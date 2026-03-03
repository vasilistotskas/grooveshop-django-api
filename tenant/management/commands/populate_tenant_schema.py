from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.db import connection

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Copy data from public schema to the webside tenant schema. "
        "Idempotent: skips tables that already have data in the target."
    )

    def handle(self, *args, **options):
        schema = "webside"

        if not self._schema_exists(schema):
            self.stdout.write(
                self.style.WARNING(
                    f"Schema '{schema}' does not exist. "
                    f"Run migrate_schemas first."
                )
            )
            return

        tables = self._get_tables(schema)
        if not tables:
            self.stdout.write(
                self.style.WARNING(f"No tables found in '{schema}' schema.")
            )
            return

        self.stdout.write(f"Found {len(tables)} tables in '{schema}' schema.")

        copied = 0
        skipped = 0

        with connection.cursor() as cursor:
            cursor.execute("SET session_replication_role = 'replica'")

            try:
                for table in tables:
                    if self._table_has_data(cursor, schema, table):
                        self.stdout.write(
                            f"  SKIP {table} (already has data in '{schema}')"
                        )
                        skipped += 1
                        continue

                    public_count = self._get_count(cursor, "public", table)
                    if public_count == 0:
                        self.stdout.write(f"  SKIP {table} (empty in 'public')")
                        skipped += 1
                        continue

                    cursor.execute(
                        f'INSERT INTO {schema}."{table}" '
                        f'SELECT * FROM public."{table}"'
                    )

                    self._fix_sequences(cursor, schema, table)

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  COPY {table} ({public_count} rows)"
                        )
                    )
                    copied += 1
            finally:
                cursor.execute("SET session_replication_role = 'DEFAULT'")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {copied} tables copied, {skipped} skipped."
            )
        )

    def _schema_exists(self, schema: str) -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM information_schema.schemata "
                "WHERE schema_name = %s",
                [schema],
            )
            return cursor.fetchone() is not None

    def _get_tables(self, schema: str) -> list[str]:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name "
                "FROM information_schema.tables "
                "WHERE table_schema = %s "
                "AND table_type = 'BASE TABLE' "
                "AND table_name != 'django_migrations' "
                "ORDER BY table_name",
                [schema],
            )
            return [row[0] for row in cursor.fetchall()]

    def _table_has_data(self, cursor, schema: str, table: str) -> bool:
        cursor.execute(
            f'SELECT EXISTS(SELECT 1 FROM {schema}."{table}" LIMIT 1)'
        )
        return cursor.fetchone()[0]

    def _get_count(self, cursor, schema: str, table: str) -> int:
        cursor.execute(f'SELECT COUNT(*) FROM {schema}."{table}"')
        return cursor.fetchone()[0]

    def _fix_sequences(self, cursor, schema: str, table: str) -> None:
        cursor.execute(
            "SELECT pg_get_serial_sequence(%s, 'id')",
            [f'{schema}."{table}"'],
        )
        row = cursor.fetchone()
        if row and row[0]:
            seq = row[0]
            cursor.execute(f'SELECT MAX(id) FROM {schema}."{table}"')
            max_id = cursor.fetchone()[0]
            if max_id is not None:
                cursor.execute(
                    "SELECT setval(%s, %s)",
                    [seq, max_id],
                )
