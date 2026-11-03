"""Give each store its theme document, from the storefront's computed seed.

The storefront turns a store's theme inputs (``export_theme_fields``) into the
theme's root settings (``pnpm theme:seed``). This command reads that output:

    {"stores": [{"schema": "...", "settings": {...}}]}

For each store it validates the settings against THAT STORE'S catalogue view
(the theme schema, then the release checks: references and the contrast
``error`` rules) and, unless ``--dry-run``, creates the theme document and
publishes it in a release that keeps every other document as it was. A store
that already has a theme document is skipped, never overwritten.

    uv run python manage.py import_theme_settings themes.json --dry-run
    uv run python manage.py import_theme_settings themes.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import tenant_context

from builder import seeding
from builder.errors import BuilderError, DocumentInvalid
from builder.models import THEME_KEY, BuilderDocument
from builder.seeding import THEME_NOTE
from tenant.models import Tenant


def read(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """``[(schema, settings)]`` from the file, or ``CommandError``: the
    whole file is judged before any store is touched."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        stores = payload["stores"]
        parsed = [(str(item["schema"]), item["settings"]) for item in stores]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CommandError(
            f"{path} is not {{stores: [{{schema, settings}}]}}"
        ) from exc
    schemas = [schema for schema, _ in parsed]
    if len(set(schemas)) != len(schemas):
        raise CommandError("a store appears twice")
    if not all(isinstance(settings_, dict) for _, settings_ in parsed):
        raise CommandError("every settings must be an object")
    known = set(Tenant.objects.values_list("schema_name", flat=True))
    if unknown := sorted(set(schemas) - known):
        raise CommandError(f"no such store: {', '.join(unknown)}")
    return parsed


class Command(BaseCommand):
    help = "Create and publish each store's theme document from a seed file."

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate against each store's catalogue and write nothing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        failed = 0
        for schema, settings_ in read(options["file"]):
            tenant = Tenant.objects.get(schema_name=schema)
            with tenant_context(tenant):
                failed += not self.one(schema, settings_, dry_run=dry_run)
        if failed:
            raise CommandError(f"{failed} store(s) failed; see above.")

    def one(
        self, schema: str, settings_: dict[str, Any], *, dry_run: bool
    ) -> bool:
        data = {"sections": {}, "order": [], "settings": settings_}
        if (
            BuilderDocument.objects.all_with_deleted()
            .filter(key=THEME_KEY)
            .exists()
        ):
            self.stdout.write(f"{schema}: skipped (it has a theme document)")
            return True
        try:
            if dry_run:
                problems = seeding.installable_problems(THEME_KEY, data, {})
                if problems:
                    raise DocumentInvalid(problems)
                self.stdout.write(f"{schema}: valid, would be published")
                return True
            release = seeding.install_document(
                THEME_KEY, data, {}, note=THEME_NOTE
            )
        except DocumentInvalid as exc:
            self.stdout.write(f"{schema}: FAILED")
            for issue in exc.issues:
                self.stdout.write(
                    f"    {issue.path} {issue.code}: {issue.message}"
                )
            return False
        except BuilderError as exc:
            self.stdout.write(f"{schema}: FAILED ({exc.detail})")
            return False
        self.stdout.write(
            f"{schema}: published in release "
            f"{release.number if release else '-'}"
        )
        return True
