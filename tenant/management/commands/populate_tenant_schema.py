from __future__ import annotations

import logging
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

logger = logging.getLogger(__name__)

# Tables that are seeded automatically by post_migrate signals, data
# migrations, or other bootstrap hooks when ``migrate_schemas`` creates a
# fresh tenant schema. For these tables the freshly-seeded rows are
# discarded and replaced with the values that live in the public schema.
#
# ONLY add a table here when public is genuinely its source of truth.
# The PreSync hook runs ``populate_tenant_schema`` (defaulting to
# ``--schema=webside``) on EVERY deploy, so an entry in this set is
# truncated and re-copied every time: any edit an operator makes to that
# table through Django admin is silently reverted on the next release.
#
# ``shipping_shippingprovider`` was briefly added here to fix the
# cutover — it is seeded ``is_active=false`` by its data migration, the
# copy skipped it as "already has data", every carrier stayed inactive
# and ``/api/v1/shipping/options`` returned ``[]`` so the checkout's
# delivery step rendered empty (production, 2026-08-20). But carrier
# rows ARE operator-editable (priority, is_active, the
# ``metadata['station_origin']`` override, logos), so overwriting them
# every deploy trades a one-time cutover bug for a permanent one. The
# divergence WARNING below is the correct guard: it surfaces the same
# problem loudly, once, and leaves the fix to a human.
#
# The truncate is CASCADE-safe for this entry: extra_settings_setting is
# not referenced by a FK from any other table, so the cascade never
# deletes anything extra.
_OVERWRITE_TABLES: frozenset[str] = frozenset({"extra_settings_setting"})

# Sentinel: distinguishes "no default could be resolved" from a field
# whose default legitimately IS None.
_NO_DEFAULT = object()

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
        diverged_skips: list[str] = []
        reset_sequences: list[tuple[str, str, str, str]] = []

        # One transaction for the whole copy loop: Django creates every
        # FK constraint as DEFERRABLE INITIALLY DEFERRED, so validation
        # happens once at COMMIT and the (alphabetical) table order is
        # irrelevant. This deliberately replaces the old
        # ``SET session_replication_role = 'replica'`` trick — that
        # parameter is superuser-only (the cluster app user gets
        # ``permission denied``) and, worse, it *skips* FK validation
        # entirely; deferral still enforces it. All-or-nothing commit
        # also makes crashed runs cleanly re-runnable.
        with transaction.atomic(), connection.cursor() as cursor:
            for table in tables:
                # Tenant-only apps (allauth account, knox, orders, …)
                # have no public-schema tables on a fresh deployment —
                # public only carries SHARED_APPS. Only the legacy
                # single-tenant cutover database has every table in
                # public. Skip anything with no source relation.
                if not self._table_exists(cursor, "public", table):
                    self.stdout.write(
                        f"  SKIP {table} (not present in 'public')"
                    )
                    skipped += 1
                    continue

                # Always-overwrite tables: truncate first, then copy.
                if table in _OVERWRITE_TABLES:
                    # REFUSE to truncate anything another table points at.
                    # TRUNCATE ... CASCADE silently deletes every
                    # referencing row, so one wrong entry in
                    # _OVERWRITE_TABLES destroys unrelated business data.
                    # This is not hypothetical: shipping_shippingprovider
                    # was added to that set on 2026-08-21 with a comment
                    # asserting it had no inbound FKs. It had two —
                    # order_order and pay_way_paywayshippingexclusion —
                    # and the PreSync hook wiped 15 tables in production,
                    # including every order. The invariant is now checked
                    # against the live catalog instead of trusted to a
                    # comment.
                    referencing = self._inbound_foreign_keys(
                        cursor, schema, table
                    )
                    if referencing:
                        raise CommandError(
                            f'{schema}."{table}" is listed in '
                            "_OVERWRITE_TABLES but is referenced by a "
                            "FOREIGN KEY from: "
                            + ", ".join(referencing)
                            + ". TRUNCATE ... CASCADE would delete those "
                            "rows too. Remove it from _OVERWRITE_TABLES — "
                            "the divergence warning is the correct guard "
                            "for a table that cannot be safely replaced."
                        )
                    self.stdout.write(
                        f"  OVERWRITE {table} (always-replace table)"
                    )
                    cursor.execute(f'TRUNCATE {schema}."{table}" CASCADE')
                elif self._table_has_data(cursor, schema, table):
                    # Seeded tables keep their DEFAULTS when skipped, so
                    # the operator's real config never arrives. Say so
                    # loudly when the contents actually diverge — a
                    # silent SKIP line in a 200-table log is how the
                    # broken-checkout regression reached production.
                    tenant_count = self._get_count(cursor, schema, table)
                    public_count = self._get_count(cursor, "public", table)
                    note = f"  SKIP {table} (already has data in '{schema}')"
                    if tenant_count != public_count:
                        note = self.style.WARNING(
                            f"{note} — DIVERGES: public has "
                            f"{public_count} rows, {schema} has "
                            f"{tenant_count}. If this table is "
                            "operator-editable, add it to "
                            "_OVERWRITE_TABLES; its seeded defaults are "
                            "being kept instead of production's values."
                        )
                        diverged_skips.append(table)
                    self.stdout.write(note)
                    skipped += 1
                    continue

                public_count = self._get_count(cursor, "public", table)
                if public_count == 0:
                    self.stdout.write(f"  SKIP {table} (empty in 'public')")
                    skipped += 1
                    continue

                target_cols, select_exprs, params = self._copy_column_lists(
                    cursor, schema, table
                )
                cursor.execute(
                    f'INSERT INTO {schema}."{table}" ({target_cols}) '
                    f'SELECT {select_exprs} FROM public."{table}"',
                    params,
                )

                seqs = self._fix_sequences(cursor, schema, table)
                reset_sequences.extend(seqs)

                self.stdout.write(
                    self.style.SUCCESS(f"  COPY {table} ({public_count} rows)")
                )
                copied += 1
                copied_tables.append(table)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {copied} tables copied, {skipped} skipped."
            )
        )

        if diverged_skips:
            self.stdout.write(
                self.style.WARNING(
                    "\nREVIEW REQUIRED — these tables were skipped but "
                    "their contents differ from 'public', so the tenant "
                    "kept seeded defaults instead of production values:\n"
                    + "".join(f"  - {t}\n" for t in diverged_skips)
                    + "Django-derived tables (django_content_type, "
                    "auth_permission) are expected here and are "
                    "self-consistent. Anything operator-editable is a "
                    "REGRESSION — add it to _OVERWRITE_TABLES and re-run."
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

    def _copy_column_lists(
        self, cursor, schema: str, table: str
    ) -> tuple[str, str, list]:
        """Build explicit (target, select, params) lists for the copy.

        ``INSERT INTO tgt SELECT * FROM src`` maps columns POSITIONALLY
        and assumes identical types — both break against a restored
        legacy database: a table that accreted ALTERs over years can
        have a different physical column order than a fresh migration
        replay (same-typed columns would swap SILENTLY), and third-party
        apps change column types between releases without a migration
        reaching the legacy table (observed: django-extra-settings
        ``value_json`` text → jsonb, which crashed the cutover dry-run).

        Copy by NAME over the intersection of both schemas' columns,
        casting any source column whose type differs from the target's.
        Source-only (dropped) columns are ignored.

        TARGET-ONLY columns need care. A column the tenant schema has and
        legacy ``public`` does not is the normal shape of a cutover:
        ``TENANT_APPS`` migrations only ever ran against tenant schemas,
        so any field added after the legacy database was last migrated is
        missing from the source. Three cases:

        - nullable            → omit; the copy leaves it NULL.
        - NOT NULL w/ default → omit; Postgres applies the default.
        - NOT NULL, NO default → MUST be supplied. Django's standard
          ``AddField`` pattern adds the column with a default and then
          drops it, so the finished column is NOT NULL with no DB
          default. Omitting it inserts NULL and the copy dies on the
          constraint — which is exactly how the production cutover
          failed on ``order_order.loyalty_discount`` (added by
          ``order.0045``, a TENANT_APPS migration that never touched
          ``public``). Take the value from the Django model field's own
          default, and refuse to continue if there isn't one rather than
          inserting a NULL that violates the constraint.
        """
        cursor.execute(
            """
            SELECT
                c_tgt.column_name,
                c_tgt.udt_name AS tgt_type,
                c_src.udt_name AS src_type
            FROM information_schema.columns c_tgt
            JOIN information_schema.columns c_src
              ON c_src.table_schema = 'public'
             AND c_src.table_name  = c_tgt.table_name
             AND c_src.column_name = c_tgt.column_name
            WHERE c_tgt.table_schema = %s
              AND c_tgt.table_name = %s
            ORDER BY c_tgt.ordinal_position
            """,
            [schema, table],
        )
        rows = cursor.fetchall()

        def sql_type(udt: str) -> str:
            # information_schema reports array types as ``_elem``;
            # cast syntax needs ``elem[]``.
            return f"{udt[1:]}[]" if udt.startswith("_") else udt

        cols = [f'"{name}"' for name, _, _ in rows]
        exprs = [
            f'"{name}"' if tgt == src else f'"{name}"::{sql_type(tgt)}'
            for name, tgt, src in rows
        ]
        params: list = []

        for name, udt in self._required_target_only_columns(
            cursor, schema, table
        ):
            default = self._model_field_default(table, name)
            if default is _NO_DEFAULT:
                raise CommandError(
                    f'{schema}."{table}".{name} is NOT NULL with no '
                    "database default and no source column in 'public', "
                    "and no Django model field default could be resolved "
                    "for it. Copying would insert NULL and violate the "
                    "constraint. Add the column to the source table with "
                    "an appropriate default, or give the model field a "
                    "default, then re-run."
                )
            cols.append(f'"{name}"')
            exprs.append(f"CAST(%s AS {sql_type(udt)})")
            params.append(default)

        return ", ".join(cols), ", ".join(exprs), params

    def _inbound_foreign_keys(
        self, cursor, schema: str, table: str
    ) -> list[str]:
        """Return ``other_table.column`` for every FK pointing at *table*.

        Read from the live catalog rather than assumed, because this is
        the guard that decides whether a TRUNCATE ... CASCADE is safe.
        """
        cursor.execute(
            """
            SELECT tc.table_name || '.' || kcu.column_name
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage kcu
                ON kcu.constraint_name = tc.constraint_name
               AND kcu.table_schema = tc.table_schema
              JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
               AND ccu.table_schema = tc.table_schema
             WHERE tc.constraint_type = 'FOREIGN KEY'
               AND tc.table_schema = %s
               AND ccu.table_name = %s
               AND tc.table_name <> %s
             ORDER BY 1
            """,
            [schema, table, table],
        )
        return [row[0] for row in cursor.fetchall()]

    def _required_target_only_columns(
        self, cursor, schema: str, table: str
    ) -> list[tuple[str, str]]:
        """Target columns that are NOT NULL, defaultless and unsourced."""
        cursor.execute(
            """
            SELECT c_tgt.column_name, c_tgt.udt_name
            FROM information_schema.columns c_tgt
            WHERE c_tgt.table_schema = %s
              AND c_tgt.table_name = %s
              AND c_tgt.is_nullable = 'NO'
              AND c_tgt.column_default IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM information_schema.columns c_src
                  WHERE c_src.table_schema = 'public'
                    AND c_src.table_name = c_tgt.table_name
                    AND c_src.column_name = c_tgt.column_name
              )
            ORDER BY c_tgt.ordinal_position
            """,
            [schema, table],
        )
        return list(cursor.fetchall())

    def _model_field_default(self, table: str, column: str):
        """Resolve a Django field default for ``table.column``.

        ``include_auto_created`` catches M2M through tables, which have
        no hand-written model but do have concrete fields. Money fields
        return a ``Money``; the column stores the bare amount.
        """
        from django.apps import apps  # noqa: PLC0415

        for model in apps.get_models(include_auto_created=True):
            if model._meta.db_table != table:
                continue
            for field in model._meta.concrete_fields:
                if field.column != column or not field.has_default():
                    continue
                default = field.get_default()
                return getattr(default, "amount", default)
        return _NO_DEFAULT

    def _table_exists(self, cursor, schema: str, table: str) -> bool:
        cursor.execute(
            "SELECT EXISTS("
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s)",
            [schema, table],
        )
        return cursor.fetchone()[0]

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
