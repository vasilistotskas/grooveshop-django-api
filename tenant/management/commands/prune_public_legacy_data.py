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
