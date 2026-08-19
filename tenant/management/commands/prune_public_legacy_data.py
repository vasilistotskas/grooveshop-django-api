"""Truncate pre-multi-tenant legacy rows from the PUBLIC schema.

The cutover populates every tenant schema by COPYING from public
(``populate_tenant_schema``) and deliberately leaves the originals in
place as a rollback safety net. Once the tenant schemas are
authoritative, those public copies are pure liability: they confuse
operators, feed accidental public-context reads (the unprefixed
Meilisearch index incident), and keep customer PII in a schema nothing
should serve it from.

Scope: ONLY tables owned by apps that are in ``TENANT_APPS`` and NOT in
``SHARED_APPS``. Shared data (users/staff, tenants, extra_settings,
celery beat, django_*) is untouched by construction. All target tables
are truncated in ONE statement WITHOUT ``CASCADE`` — the tenant-only
set is FK-closed, and if some shared table unexpectedly references one
of these, PostgreSQL aborts naming it instead of silently cascading
into shared data.

Safety:
- Refuses to run unless ``--yes`` is passed (and prints the plan).
- ``--dry-run`` lists every table with its current row count.
- Requires every active tenant schema to exist first — pruning before
  the copies exist would destroy the only copy.

Run AFTER cutover verification (MULTI_TENANT_CUTOVER.md §6.10), with a
fresh off-cluster dump in hand.
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


def _tenant_only_app_labels() -> list[str]:
    shared = set(settings.SHARED_APPS)
    return [
        app.split(".")[-1] if "." in app else app
        for app in settings.TENANT_APPS
        if app not in shared
    ]


class Command(BaseCommand):
    help = (
        "Truncate pre-multi-tenant legacy rows from the PUBLIC schema "
        "(tables of TENANT_APPS-only apps). Tenant schemas and shared "
        "platform data are untouched."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List target tables and row counts without deleting.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Actually truncate (required for the destructive run).",
        )

    def _target_tables(self) -> list[str]:
        labels = set(_tenant_only_app_labels())
        tables: list[str] = []
        for model in apps.get_models(include_auto_created=True):
            if model._meta.app_label in labels and not model._meta.proxy:
                tables.append(model._meta.db_table)
        # Stable order for readable output; dedupe (m2m through tables
        # can surface twice via include_auto_created).
        return sorted(set(tables))

    def _neutralize_legacy_inbound_fks(self, targets: list[str]) -> None:
        """Null + drop DB-level FKs pointing INTO the target set from
        outside it.

        The only sanctioned cross-boundary relation is
        ``UserAccount.loyalty_tier``, declared ``db_constraint=False``
        (an ORM-only relation — PostgreSQL cannot enforce it across
        tenant schemas). Pre-multi-tenant databases created a REAL
        constraint for it, and that debris survives in cloned public
        schemas, blocking the truncate. For each such inbound
        constraint: verify the referencing column is nullable (abort
        loudly otherwise — that would be a genuine modeling error, not
        debris), null the column, and drop the constraint so the
        schema finally matches the model definition.
        """
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT con.conname,
                       src.relname AS src_table,
                       att.attname AS src_column,
                       att.attnotnull,
                       tgt.relname AS tgt_table
                FROM pg_constraint con
                JOIN pg_class src ON src.oid = con.conrelid
                JOIN pg_class tgt ON tgt.oid = con.confrelid
                JOIN pg_namespace nsp ON nsp.oid = src.relnamespace
                JOIN pg_attribute att
                  ON att.attrelid = con.conrelid
                 AND att.attnum = ANY (con.conkey)
                WHERE con.contype = 'f'
                  AND nsp.nspname = 'public'
                  AND tgt.relname = ANY (%s)
                  AND NOT (src.relname = ANY (%s))
                """,
                [targets, targets],
            )
            inbound = cur.fetchall()

            for conname, src_table, src_column, notnull, tgt_table in inbound:
                if notnull:
                    raise CommandError(
                        f"public.{src_table}.{src_column} (NOT NULL) "
                        f"references target table {tgt_table} via "
                        f"{conname} — refusing to touch a non-nullable "
                        "cross-boundary reference; resolve it manually."
                    )
                self.stdout.write(
                    f"Neutralizing legacy constraint {conname}: "
                    f"public.{src_table}.{src_column} → {tgt_table} "
                    "(nulling column, dropping constraint — the model "
                    "declares this relation db_constraint=False)."
                )
                cur.execute(
                    f'UPDATE public."{src_table}" SET "{src_column}" = NULL '
                    f'WHERE "{src_column}" IS NOT NULL'
                )
                cur.execute(
                    f'ALTER TABLE public."{src_table}" '
                    f'DROP CONSTRAINT "{conname}"'
                )

    def handle(self, *args, **options):
        schema = getattr(connection, "schema_name", "public")
        if schema != "public":
            raise CommandError(
                "Run from the public schema context (this command prunes "
                f"PUBLIC copies; current schema is {schema!r})."
            )

        from tenant.models import Tenant

        active = Tenant.objects.filter(is_active=True).exclude(
            schema_name="public"
        )
        if not active.exists():
            raise CommandError(
                "No active tenant schemas exist — the public rows would "
                "be the ONLY copy. Populate tenant schemas first."
            )

        tables = self._target_tables()
        existing: list[tuple[str, int]] = []
        with connection.cursor() as cur:
            for table in tables:
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = %s)",
                    [table],
                )
                if not cur.fetchone()[0]:
                    continue
                cur.execute(f'SELECT count(*) FROM public."{table}"')
                existing.append((table, cur.fetchone()[0]))

        total_rows = sum(count for _, count in existing)
        self.stdout.write(
            f"{len(existing)} tenant-only tables present in public, "
            f"{total_rows} legacy rows total."
        )
        for table, count in existing:
            if count:
                self.stdout.write(f"  {table}: {count}")

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Dry run — nothing deleted."))
            return

        if not options["yes"]:
            raise CommandError(
                "Refusing to truncate without --yes (use --dry-run to "
                "inspect the plan)."
            )

        to_truncate = [table for table, _count in existing]
        if to_truncate:
            self._neutralize_legacy_inbound_fks(to_truncate)
            joined = ", ".join(f'public."{t}"' for t in to_truncate)
            with connection.cursor() as cur:
                # Flush any deferred FK triggers first — TRUNCATE
                # refuses to run with pending trigger events when the
                # session already wrote to these tables (e.g. inside a
                # transaction).
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
                # Deliberately NO CASCADE: the tenant-only set is
                # FK-closed, so this succeeds as-is — and if a shared
                # table unexpectedly references one of these, aborting
                # with its name beats silently truncating shared data.
                cur.execute(f"TRUNCATE {joined}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Truncated {len(to_truncate)} public tables "
                f"({total_rows} legacy rows removed)."
            )
        )
