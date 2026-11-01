"""Write what the converter would be sent, one JSON file per layout and
menu, as a corpus for the storefront's tests.

    manage.py builder_export_legacy --all-tenants --out corpus/

``<out>/<schema>/<document key>.json`` holds the exact request body of
``POST /api/builder/convert-legacy`` (``builder.legacy``). Page layouts and
menus hold no personal data. Reads only.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from builder import legacy, storefront
from core.management.tenant_mixin import TenantCommandMixin
from core.storefront import StorefrontUnavailable


class Command(TenantCommandMixin, BaseCommand):
    help = "Export legacy page layouts and menus as converter requests."
    require_tenant_scope = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)
        parser.add_argument("--out", required=True, help="Output directory.")

    def handle(self, *args, **options):
        written = 0
        for context in self.iter_tenant_contexts(options):
            with context:
                try:
                    templates = storefront.fetch_catalogue(
                        connection.schema_name
                    ).templates
                except StorefrontUnavailable as exc:
                    raise CommandError(
                        f"storefront unavailable: {exc}"
                    ) from exc
                folder = Path(options["out"]) / connection.schema_name
                folder.mkdir(parents=True, exist_ok=True)
                for key, _published, body in legacy.convert_requests(
                    legacy.store_locales(), templates
                ):
                    request = {
                        "schema": connection.schema_name,
                        "defaultLocale": settings.PARLER_DEFAULT_LANGUAGE_CODE,
                        **body,
                    }
                    name = key.replace(":", "__") + ".json"
                    (folder / name).write_text(
                        json.dumps(
                            request,
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    written += 1
        self.stdout.write(self.style.SUCCESS(f"wrote {written} request(s)."))
