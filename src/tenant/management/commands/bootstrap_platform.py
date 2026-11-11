from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Idempotently bootstrap the PUBLIC-schema platform tenant row "
        "and its primary domain — the host platform staff use to reach "
        "/admin/ as platform identities (see tenant.auth_backends."
        "PlatformStaffBackend). Run once per environment: "
        "`manage.py bootstrap_platform --domain platform.grooveshop.space` "
        "(staging: `platform-staging.grooveshop.space`). Safe to re-run: "
        "re-running with the SAME --domain is a no-op beyond confirming "
        "the row/domain exist. Re-running with a DIFFERENT --domain adds "
        "it as the new primary domain and demotes the previous one "
        "(single-primary semantics enforced by TenantDomain.save())."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            required=True,
            help=(
                "Domain that resolves to the PUBLIC schema — the "
                "platform admin host (e.g. platform.grooveshop.space)."
            ),
        )

    def handle(self, *args, **options):
        from tenant.models import Tenant, TenantDomain

        domain = options["domain"]
        schema_name = get_public_schema_name()

        try:
            tenant = Tenant.objects.get(schema_name=schema_name)
            created = False
        except Tenant.DoesNotExist:
            tenant = Tenant(
                schema_name=schema_name,
                name="Platform",
                slug="platform",
                owner_email=f"platform@{domain}",
                store_name="Platform",
                is_active=True,
            )
            # The public schema always exists (it's Postgres' own
            # default schema) — there is nothing to CREATE. Set this
            # on the instance BEFORE save() so TenantMixin.save() never
            # attempts schema creation/migration for this row.
            tenant.auto_create_schema = False
            tenant.save()
            created = True

        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created public tenant row (schema={schema_name!r})."
                )
            )
        else:
            self.stdout.write(
                f"Public tenant row already exists (schema={schema_name!r})."
            )

        domain_row, domain_created = TenantDomain.objects.get_or_create(
            domain=domain,
            defaults={"tenant": tenant, "is_primary": True},
        )
        if domain_created:
            self.stdout.write(
                self.style.SUCCESS(f"Added domain {domain!r} (primary).")
            )
        elif domain_row.tenant_id != tenant.pk:
            raise CommandError(
                f"Domain {domain!r} already exists but belongs to a "
                f"different tenant (schema="
                f"{domain_row.tenant.schema_name!r})."
            )
        elif not domain_row.is_primary:
            # Re-affirm as primary — TenantDomain.save() (DomainMixin)
            # demotes any sibling domains of this tenant automatically.
            domain_row.is_primary = True
            domain_row.save()
            self.stdout.write(
                f"Re-affirmed {domain!r} as the primary domain "
                "(demoted the previous primary)."
            )
        else:
            self.stdout.write(
                f"Domain {domain!r} is already the primary domain — no changes."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Platform bootstrap complete: schema={schema_name!r}, "
                f"domain={domain!r}."
            )
        )
