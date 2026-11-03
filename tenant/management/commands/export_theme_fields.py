"""Write every store's current theme fields as JSON, changing nothing.

The page builder's theme document is seeded from what each store renders
today. The storefront computes the seed (``pnpm theme:seed``), because the
rules that turn a preset, a Tailwind colour name and ``theme_metadata`` into a
theme live there; this command only reads the inputs:

    {"stores": [{"schema": "...", "fields": {
        "schemaName", "themePreset", "primaryColor", "neutralColor",
        "accentHex", "successHex", "warningHex", "errorHex", "infoHex",
        "themeMetadata"}}]}

The field names are the camelCase ones the storefront reads from the tenant
config. The platform's own tenant is left out.

    uv run python manage.py export_theme_fields > theme-fields.json
    uv run python manage.py export_theme_fields --tenant webside
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django_tenants.utils import get_public_schema_name

from tenant.models import Tenant

#: Tenant column -> the name the storefront reads.
FIELDS = {
    "schema_name": "schemaName",
    "theme_preset": "themePreset",
    "primary_color": "primaryColor",
    "neutral_color": "neutralColor",
    "accent_hex": "accentHex",
    "success_hex": "successHex",
    "warning_hex": "warningHex",
    "error_hex": "errorHex",
    "info_hex": "infoHex",
    "theme_metadata": "themeMetadata",
}


def export(schema: str | None = None) -> dict:
    stores = Tenant.objects.exclude(
        schema_name=get_public_schema_name()
    ).order_by("schema_name")
    if schema is not None:
        stores = stores.filter(schema_name=schema)
    return {
        "stores": [
            {
                "schema": store.schema_name,
                "fields": {
                    name: getattr(store, column)
                    for column, name in FIELDS.items()
                },
            }
            for store in stores
        ]
    }


class Command(BaseCommand):
    help = "Write each store's theme fields as JSON. Reads only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant", help="Only this store's schema (default: every store)."
        )

    def handle(self, *args, **options):
        self.stdout.write(
            json.dumps(export(options["tenant"]), indent=2, ensure_ascii=False)
        )
