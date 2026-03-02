"""Mixin adding tenant-aware arguments to Meilisearch management commands."""

from django.core.management.base import CommandError


class TenantCommandMixin:
    """Mixin adding --tenant and --all-tenants flags to management commands."""

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
        return [None]  # None = use current connection context
