from __future__ import annotations

import logging
import sys

from django.core.management.base import BaseCommand
from django.db import connection

logger = logging.getLogger(__name__)

# Tables that are seeded automatically by post_migrate signals (or other
# bootstrap hooks) when ``migrate_schemas`` creates a fresh tenant schema.
# For these tables the freshly-seeded rows must be discarded and replaced
# with the production-tuned values that live in the public schema.
#
# The truncate is CASCADE-safe: extra_settings_setting is never referenced
# by a FK from another table so the cascade never deletes anything extra.
_OVERWRITE_TABLES: frozenset[str] = frozenset({"extra_settings_setting"})

# MPTT models that need a tree rebuild after copy.
# Stored as ``"app_label.ModelName"`` strings so we can import them lazily
# without a hard dependency at module level.
_MPTT_MODELS: tuple[str, ...] = (
    "product.ProductCategory",
    "blog.BlogCategory",
    "blog.BlogComment",
)


class Command(BaseCommand):
    """One-shot bootstrap helper to copy public-schema seed data into a
    tenant schema after ``migrate_schemas`` has created it.

    This is NOT intended for routine operations — run it once per new
    tenant when you need to populate lookup tables (countries, VAT rates,
    pay-ways, etc.) that are maintained in the public schema during
    development and then cloned into each tenant on first boot.

    Idempotency
    -----------
    Most tables are skipped when they already contain rows in the target
    schema.  Tables listed in ``_OVERWRITE_TABLES`` (currently only
    ``extra_settings_setting``) are *always* truncated-then-replaced so
    that production-tuned values win over the freshly-seeded defaults that
    ``migrate_schemas`` injects via post_migrate signals.

    Sequences
    ---------
    After copying every table, ALL sequences owned by that table's columns
    are reset to ``MAX(column)`` so that the next INSERT never conflicts.
    This covers:

    * Regular ``id`` sequences
    * ``history_id`` on django-simple-history tables
    * ``tree_id`` on django-mptt tables
    * Any other auto-generated column sequence

    MPTT integrity
    --------------
    MPTT ``lft``/``rght``/``level``/``tree_id`` values are copied as-is
    from the (presumed-clean) public schema.  As a safety net, pass
    ``--rebuild-mptt`` (enabled by default) to call
    ``Model.objects.rebuild()`` for every known MPTT model after the copy
    loop.  Set ``--no-rebuild-mptt`` to skip when you are certain the
    source data is clean and the rebuild cost is not acceptable.

    Verification
    ------------
    After the copy loop, a verification pass checks that row counts match
    between public and the target schema for every copied table, and that
    each reset sequence's ``last_value`` matches ``MAX(column)``.  Any
    mismatch is printed as FAIL and the command exits with status 1 so
    operators know to investigate before serving traffic.
    """

    help = (
        "Copy data from the public schema into a tenant schema. "
        "Idempotent for most tables; always overwrites "
        "extra_settings_setting. One-shot bootstrap helper — not for "
        "routine use."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            default="webside",
            help=(
                "Target tenant schema name (default: webside). "
                "Must already exist — run migrate_schemas first."
            ),
        )
        parser.add_argument(
            "--rebuild-mptt",
            action="store_true",
            default=True,
            dest="rebuild_mptt",
            help=(
                "Call Model.objects.rebuild() for every known MPTT model "
                "after the copy loop (default: True)."
            ),
        )
        parser.add_argument(
            "--no-rebuild-mptt",
            action="store_false",
            dest="rebuild_mptt",
            help="Skip the MPTT rebuild step.",
        )

    def handle(self, *args, **options):
        schema = options["schema"]
        rebuild_mptt = options["rebuild_mptt"]

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
        # Track (schema, table, seq_name, col_name) for post-copy
        # verification.
        copied_tables: list[str] = []
        reset_sequences: list[tuple[str, str, str, str]] = []

        with connection.cursor() as cursor:
            cursor.execute("SET session_replication_role = 'replica'")

            try:
                for table in tables:
                    # Always-overwrite tables: truncate first, then copy.
                    if table in _OVERWRITE_TABLES:
                        self.stdout.write(
                            f"  OVERWRITE {table} (always-replace table)"
                        )
                        cursor.execute(f'TRUNCATE {schema}."{table}" CASCADE')
                    elif self._table_has_data(cursor, schema, table):
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

                    seqs = self._fix_sequences(cursor, schema, table)
                    reset_sequences.extend(seqs)

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  COPY {table} ({public_count} rows)"
                        )
                    )
                    copied += 1
                    copied_tables.append(table)
            finally:
                cursor.execute("SET session_replication_role = 'origin'")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {copied} tables copied, {skipped} skipped."
            )
        )

        if rebuild_mptt and copied_tables:
            self._rebuild_mptt_trees(schema)

        # Verification pass — always run; exit non-zero on any FAIL.
        all_passed = self._verify(schema, copied_tables, reset_sequences)
        if not all_passed:
            self.stderr.write(
                self.style.ERROR(
                    "\nVerification FAILED — investigate before "
                    "serving traffic."
                )
            )
            sys.exit(1)

    # ------------------------------------------------------------------
    # Schema / table helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Sequence management (B1)
    # ------------------------------------------------------------------

    def _discover_sequences(
        self,
        cursor,
        schema: str,
        table: str,
    ) -> list[tuple[str, str]]:
        """Return all (sequence_name, column_name) pairs owned by *table*.

        Uses ``pg_depend`` to find every sequence that was created as
        part of a SERIAL / BIGSERIAL / GENERATED BY DEFAULT AS IDENTITY
        column on the given table, regardless of the column name.
        """
        cursor.execute(
            """
            SELECT
                seq.relname  AS sequence_name,
                attr.attname AS column_name
            FROM pg_class seq
            JOIN pg_depend dep
                ON dep.objid = seq.oid
            JOIN pg_class tbl
                ON dep.refobjid = tbl.oid
            JOIN pg_attribute attr
                ON attr.attrelid = tbl.oid
               AND attr.attnum   = dep.refobjsubid
            JOIN pg_namespace ns
                ON ns.oid = tbl.relnamespace
            WHERE seq.relkind = 'S'
              AND ns.nspname  = %s
              AND tbl.relname = %s
            """,
            [schema, table],
        )
        return cursor.fetchall()  # list of (seq_name, col_name)

    def _fix_sequences(
        self,
        cursor,
        schema: str,
        table: str,
    ) -> list[tuple[str, str, str, str]]:
        """Reset every sequence owned by *table* to MAX(owning_column).

        Returns a list of (schema, table, seq_name, col_name) tuples for
        post-copy verification.

        The third ``setval`` argument controls whether ``nextval()`` will
        return ``last_value`` (False) or ``last_value + 1`` (True).
        We pass ``true`` so the very next INSERT gets ``max + 1``.

        For an empty table, ``COALESCE(MAX(col), 1)`` falls back to 1 and
        we pass ``false`` so ``nextval()`` returns 1 on first use rather
        than skipping to 2.
        """
        pairs = self._discover_sequences(cursor, schema, table)
        result: list[tuple[str, str, str, str]] = []
        for seq_name, col_name in pairs:
            cursor.execute(
                f'SELECT COALESCE(MAX("{col_name}"), 0) FROM {schema}."{table}"'
            )
            max_val = cursor.fetchone()[0]

            if max_val == 0:
                # Empty table: set to 1 with is_called=false so that the
                # next nextval() returns 1 (not 2).
                cursor.execute(
                    f"SELECT setval('{schema}.{seq_name}', 1, false)"
                )
            else:
                # Non-empty table: set to max with is_called=true so that
                # the next nextval() returns max + 1.
                cursor.execute(
                    f"SELECT setval('{schema}.{seq_name}', {max_val}, true)"
                )

            result.append((schema, table, seq_name, col_name))
        return result

    # ------------------------------------------------------------------
    # MPTT rebuild (B3)
    # ------------------------------------------------------------------

    def _rebuild_mptt_trees(self, schema: str) -> None:
        """Call ``Model.objects.rebuild()`` for every known MPTT model.

        ``rebuild()`` recomputes ``lft``, ``rght``, ``level``, and
        ``tree_id`` from the parent-FK relationships, making the tree
        fully consistent regardless of whether the copied rows had any
        minor drift.  The rebuild runs against whatever schema is active
        on the connection; in practice this command is run with the
        django-tenants connection set to the target schema.
        """
        self.stdout.write("\nRebuilding MPTT trees…")
        for dotted in _MPTT_MODELS:
            app_label, model_name = dotted.split(".")
            try:
                from django.apps import apps

                model = apps.get_model(app_label, model_name)
                model.objects.rebuild()
                self.stdout.write(
                    self.style.SUCCESS(f"  MPTT rebuild OK: {dotted}")
                )
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(
                    self.style.WARNING(f"  MPTT rebuild SKIP {dotted}: {exc}")
                )

    # ------------------------------------------------------------------
    # Verification (B4)
    # ------------------------------------------------------------------

    def _verify(
        self,
        schema: str,
        copied_tables: list[str],
        reset_sequences: list[tuple[str, str, str, str]],
    ) -> bool:
        """Print a verification report and return True if all checks pass."""
        if not copied_tables and not reset_sequences:
            return True

        self.stdout.write("\n--- Verification ---")
        passed = 0
        failed = 0

        # Row-count parity
        with connection.cursor() as cursor:
            for table in copied_tables:
                pub = self._get_count(cursor, "public", table)
                tgt = self._get_count(cursor, schema, table)
                if pub == tgt:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  PASS rows  {table}: public={pub} {schema}={tgt}"
                        )
                    )
                    passed += 1
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f"  FAIL rows  {table}: public={pub} {schema}={tgt}"
                        )
                    )
                    failed += 1

            # Sequence last_value vs MAX(col)
            seen: set[tuple[str, str]] = set()
            for tgt_schema, table, seq_name, col_name in reset_sequences:
                key = (seq_name, col_name)
                if key in seen:
                    continue
                seen.add(key)

                cursor.execute(
                    f"SELECT last_value, is_called FROM {tgt_schema}.{seq_name}"
                )
                last_val, is_called = cursor.fetchone()

                cursor.execute(
                    f'SELECT COALESCE(MAX("{col_name}"), 0)'
                    f' FROM {tgt_schema}."{table}"'
                )
                max_val = cursor.fetchone()[0]

                # When is_called=true the sequence has already advanced;
                # last_value should equal max_val (we set it that way).
                # When is_called=false the table was empty; last_value=1.
                ok = (is_called and last_val == max_val) or (
                    not is_called and max_val == 0
                )
                label = f"{table}.{col_name} → {seq_name}"
                if ok:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  PASS seq   {label}: "
                            f"last_value={last_val} max={max_val}"
                        )
                    )
                    passed += 1
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f"  FAIL seq   {label}: "
                            f"last_value={last_val} max={max_val}"
                        )
                    )
                    failed += 1

        self.stdout.write(f"\nVerification: {passed} passed, {failed} failed.")
        return failed == 0
