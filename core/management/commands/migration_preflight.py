"""Refuse a deploy whose pending migrations break the release still serving.

Runs as step 0 of the Argo CD PreSync hook, before ``migrate_schemas``,
and in CI after migrating a database to the base commit. Every schema
``migrate_schemas`` would touch is checked — ``public`` and every tenant
row, active or not — against the migrations already applied to it, which
are the serving release's models. Nothing is written. The rules are in
``core/db/migration_safety.py`` and ``docs/migrations.md``.

Exit status 1 names each blocking finding and the schemas it applies to;
the sync stops and the old pods keep serving. A finding on a migration
that declares ``accepted_downtime`` is printed and does not block.
"""

from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, router
from django.db.migrations.executor import MigrationExecutor
from django_tenants.utils import get_public_schema_name, get_tenant_model

from core.db.migration_safety import Finding, MigrationKey, classify


def check_schema(
    schema_name: str,
    cache: dict[
        tuple[bool, frozenset[MigrationKey]], tuple[int, list[Finding]]
    ],
) -> tuple[int, list[Finding]]:
    """Pending-migration count and findings for one schema.

    The search path is the schema alone, as django-tenants'
    ``run_migrations`` does while it ensures the recorder table, so a
    schema without ``django_migrations`` reads as fresh instead of
    falling through to ``public``'s. Tenant schemas usually share one
    applied set, so the result is cached per (public?, applied set) —
    the router's verdict depends only on which of the two it is.
    """
    connection.set_schema(schema_name, include_public=False)
    executor = MigrationExecutor(connection)
    applied = frozenset(executor.loader.applied_migrations)
    key = (schema_name == get_public_schema_name(), applied)
    if key not in cache:
        # commands/migrate.py: no targets means every leaf node.
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        findings: list[Finding] = []
        if plan:
            findings = classify(
                [migration for migration, _backwards in plan],
                # executor.py: the state ``migrate`` itself starts from.
                executor._create_project_state(with_applied_migrations=True),
                allow=lambda app_label, **hints: router.allow_migrate(
                    connection.alias, app_label, **hints
                ),
                applied=applied,
                max_length=connection.ops.max_name_length(),
            )
        cache[key] = (len(plan), findings)
    return cache[key]


def schema_names() -> list[str]:
    """``public`` first, then every tenant row — ``migrate_schemas``'s set."""
    public = get_public_schema_name()
    connection.set_schema_to_public()
    tenant_model = get_tenant_model()
    if (
        tenant_model._meta.db_table
        not in connection.introspection.table_names()
    ):
        return [public]  # a fresh bring-up: no tenant table yet
    tenants = (
        tenant_model.objects.exclude(schema_name=public)
        .order_by("schema_name")
        .values_list("schema_name", flat=True)
    )
    return [public, *tenants]


class Command(BaseCommand):
    help = (
        "Fail when a pending migration removes, renames or tightens a "
        "table or column the serving release still uses."
    )

    def handle(self, *args, **options):
        cache: dict = {}
        by_finding: dict[Finding, list[str]] = defaultdict(list)
        try:
            for schema_name in schema_names():
                pending, findings = check_schema(schema_name, cache)
                self.stdout.write(f"{schema_name}: {pending} pending")
                for finding in findings:
                    by_finding[finding].append(schema_name)
        finally:
            connection.set_schema_to_public()

        blocking = 0
        for finding, schemas in by_finding.items():
            app_label, name = finding.migration
            if finding.blocking:
                blocking += 1
                self.stderr.write(
                    f"BLOCK {app_label}.{name}: {finding.operation}\n"
                    f"      {finding.reason}\n"
                    f"      schemas: {', '.join(schemas)}"
                )
            else:
                self.stdout.write(
                    f"ACCEPTED {app_label}.{name}: {finding.operation}\n"
                    f"      {finding.reason}\n"
                    f"      accepted_downtime: {finding.accepted_downtime}\n"
                    f"      schemas: {', '.join(schemas)}"
                )
        if blocking:
            raise CommandError(
                f"{blocking} pending migration operation(s) would break the "
                "release still serving. Split them across two releases "
                "(docs/migrations.md), or deploy the intermediate release "
                "first if this deploy skips one."
            )
        self.stdout.write(self.style.SUCCESS("migration preflight: safe"))
