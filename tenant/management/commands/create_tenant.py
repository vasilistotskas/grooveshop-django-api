from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Create a new tenant with schema, domains, and seed data."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--slug", required=True)
        parser.add_argument("--schema", required=True, dest="schema_name")
        parser.add_argument("--domain", required=True)
        parser.add_argument("--owner-email", required=True)
        parser.add_argument("--plan", default="trial")
        parser.add_argument("--store-name", default="")
        parser.add_argument(
            "--extra-domains",
            nargs="*",
            default=[],
            help="Additional non-primary domains",
        )

    def handle(self, *args, **options):
        from tenant.models import Tenant, TenantDomain

        schema_name = options["schema_name"]
        slug = options["slug"]
        domain = options["domain"]

        if Tenant.objects.filter(schema_name=schema_name).exists():
            raise CommandError(
                f"Tenant with schema '{schema_name}' already exists."
            )

        if Tenant.objects.filter(slug=slug).exists():
            raise CommandError(f"Tenant with slug '{slug}' already exists.")

        self.stdout.write(f"Creating tenant '{options['name']}'...")

        tenant = Tenant.objects.create(
            schema_name=schema_name,
            name=options["name"],
            slug=slug,
            owner_email=options["owner_email"],
            plan=options["plan"],
            store_name=options["store_name"] or options["name"],
            is_active=True,
        )

        TenantDomain.objects.create(
            domain=domain,
            tenant=tenant,
            is_primary=True,
        )

        for extra in options["extra_domains"]:
            TenantDomain.objects.create(
                domain=extra,
                tenant=tenant,
                is_primary=False,
            )

        self.stdout.write(
            f"  Schema '{schema_name}' created with migrations applied."
        )

        # Seed default data in tenant schema
        with schema_context(schema_name):
            self._seed_defaults(tenant)

        self.stdout.write(
            self.style.SUCCESS(
                f"Tenant '{options['name']}' created successfully."
            )
        )

    def _seed_defaults(self, tenant):
        from django.conf import settings

        # Seed extra_settings defaults
        try:
            from extra_settings.models import Setting

            for default in getattr(settings, "EXTRA_SETTINGS_DEFAULTS", []):
                Setting.objects.get_or_create(
                    name=default["name"],
                    defaults={
                        "setting_type": default.get("type", "string"),
                        "value": str(default.get("value", "")),
                    },
                )
            logger.info(f"Seeded extra_settings for {tenant.schema_name}")
        except Exception:
            logger.warning("Could not seed extra_settings", exc_info=True)

        # Seed default page layouts
        try:
            from page_config.defaults import seed_page_layouts

            seed_page_layouts()
            logger.info("Seeded page layouts for %s", tenant.schema_name)
        except Exception:
            logger.warning("Could not seed page layouts", exc_info=True)

        # Create Meilisearch indexes for tenant
        try:
            self._create_meili_indexes(tenant)
        except Exception:
            logger.warning(
                "Could not create Meilisearch indexes",
                exc_info=True,
            )

    def _create_meili_indexes(self, tenant):
        from django.conf import settings as django_settings

        if django_settings.MEILISEARCH.get("OFFLINE"):
            return

        from meili._client import client as meili_client

        # Discover all IndexMixin subclasses
        from meili.models import IndexMixin

        for model in IndexMixin.__subclasses__():
            index_name = model.get_meili_index_name()
            pk = getattr(model.MeiliMeta, "primary_key", "id")
            try:
                meili_client.create_index(index_name, pk)
                logger.info(f"Created Meilisearch index: {index_name}")
            except Exception:
                logger.warning(
                    f"Could not create index {index_name}",
                    exc_info=True,
                )
