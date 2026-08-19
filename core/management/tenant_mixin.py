"""Mixin adding tenant-aware arguments to management commands.

Any command that reads or writes TENANT_APPS models should use this so
operators can target a schema explicitly (``--tenant <schema>`` /
``--all-tenants``). Commands whose data does not exist in the public
schema at all (notifications, orders, …) should additionally set
``require_tenant_scope = True`` — running them from the public
connection context would either crash (fresh database: the table does
not exist in public) or, worse, silently act on pre-multi-tenant
legacy rows left in public by the production clone.
"""

from django.core.management.base import CommandError


class TenantCommandMixin:
    """Mixin adding --tenant and --all-tenants flags to management commands."""

    #: When True, invoking the command from the PUBLIC connection
    #: context without --tenant/--all-tenants is an error. Callers that
    #: are already inside a tenant context (e.g. the per-tenant Celery
    #: fanout tasks, which run ``call_command`` under ``TenantTask``)
    #: pass the guard without flags.
    require_tenant_scope = False

    def add_tenant_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--tenant",
            type=str,
            help="Run for a specific tenant schema",
        )
        group.add_argument(
            "--all-tenants",
            action="store_true",
            default=False,
            help="Run for all active tenant schemas",
        )

    def get_tenant_schemas(self, options):
        from tenant.models import Tenant

        if options.get("all_tenants"):
            schemas = list(
                Tenant.objects.filter(is_active=True)
                .exclude(schema_name="public")
                .values_list("schema_name", flat=True)
            )
            if not schemas:
                raise CommandError("No active tenants found.")
            return schemas
        elif options.get("tenant"):
            schema = options["tenant"]
            if not Tenant.objects.filter(schema_name=schema).exists():
                raise CommandError(f"Tenant schema '{schema}' not found.")
            return [schema]

        if self.require_tenant_scope:
            from django.db import connection
            from django_tenants.utils import get_public_schema_name

            current = getattr(connection, "schema_name", None)
            if not current or current == get_public_schema_name():
                raise CommandError(
                    "This command operates on tenant-schema data and was "
                    "invoked from the public schema context. Pass "
                    "--tenant <schema> or --all-tenants."
                )

        return [None]  # None = use current connection context
