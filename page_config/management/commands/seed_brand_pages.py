"""Seed the brand marketing page layouts for a single tenant schema.

Opt-in counterpart to ``seed_page_layouts`` (which every tenant gets
automatically during ``tenant_create`` provisioning). The four layouts this command seeds — about / vision /
what-is-microlearning / why-microlearning — render through per-tenant
Nuxt variant components with no props, so they are NOT part of the
universal default set: only tenants that actually ship these specific
pages should get them. It also ensures the home layout exists and
fills a prop-less hero_carousel with the brand banner artwork
(``BRAND_HOME_HERO_PROPS``) — the shared component deliberately has no
built-in banner, so without this step the brand homepage renders no
hero at all.

No tenant name is hardcoded here — pass whichever schema needs them.
Referenced from ``MULTI_TENANT_CUTOVER.md`` §0.3/§6 as a webside
cutover step; also callable locally for dev.

Idempotent — safe to re-run; existing layouts (matched by
``page_type``) are left untouched.

Usage:
    manage.py seed_brand_pages --schema <schema>
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from tenant.models import Tenant


class Command(BaseCommand):
    help = "Seed the brand marketing page layouts for a tenant schema."

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema", required=True, help="Tenant schema to seed."
        )

    def handle(self, *args, **options):
        schema_name = options["schema"]
        if not Tenant.objects.filter(schema_name=schema_name).exists():
            raise CommandError(f"No tenant with schema {schema_name!r}.")

        from page_config.defaults import seed_brand_pages  # noqa: PLC0415

        with schema_context(schema_name):
            created_map = seed_brand_pages()

        for page_type, created in created_map.items():
            verb = "Created" if created else "Already existed"
            self.stdout.write(f"[{schema_name}] {verb}: {page_type}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Brand pages seeded for schema {schema_name!r}."
            )
        )
