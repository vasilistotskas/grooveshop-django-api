from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Create a default development tenant with localhost domain."

    def handle(self, *args, **options):
        from tenant.models import Tenant, TenantDomain

        schema_name = "dev"
        domain = "localhost"

        # Check if tenant already exists
        if Tenant.objects.filter(schema_name=schema_name).exists():
            tenant = Tenant.objects.get(schema_name=schema_name)
            # Ensure localhost domain exists
            if not TenantDomain.objects.filter(domain=domain).exists():
                TenantDomain.objects.create(
                    domain=domain, tenant=tenant, is_primary=True
                )
                self.stdout.write(
                    f"  Added domain '{domain}' to existing tenant."
                )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dev tenant '{schema_name}' already exists."
                )
            )
            self._seed(tenant)
            return

        self.stdout.write("Creating dev tenant...")

        tenant = Tenant.objects.create(
            schema_name=schema_name,
            name="Development Store",
            slug="dev",
            owner_email="dev@localhost",
            plan="trial",
            store_name="GrooveShop Dev",
            is_active=True,
        )

        TenantDomain.objects.create(
            domain=domain, tenant=tenant, is_primary=True
        )

        self.stdout.write(
            f"  Schema '{schema_name}' created with migrations applied."
        )
        self._seed(tenant)

        self.stdout.write(
            self.style.SUCCESS("Dev tenant created successfully.")
        )

    def _seed(self, tenant):
        with schema_context(tenant.schema_name):
            try:
                from page_config.defaults import seed_page_layouts

                seed_page_layouts()
                self.stdout.write(
                    f"  Seeded page layouts for '{tenant.schema_name}'."
                )
            except Exception as exc:
                self.stderr.write(
                    self.style.WARNING(f"  Could not seed page layouts: {exc}")
                )
