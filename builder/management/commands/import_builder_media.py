"""Bring the images that builder documents name by URL into the media library.

For one store (``--tenant``): scan the drafts and the published versions for
image paths of the store (``/img/...``, ``/uploads/...``), import each through
the library's own import (de-duplicated by content, shrunk if oversize) and
print the URL -> asset map. External addresses are listed and never fetched.

    uv run python manage.py import_builder_media --tenant webside --dry-run
    uv run python manage.py import_builder_media --tenant webside --mapping-file map.json

``--dry-run`` reads every file the real run would and writes nothing: no asset,
no file. Documents are never rewritten (``builder/media_migration.py``).
Safe to run again: an image already in the library is found by its content.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from builder import media_migration
from core.management.tenant_mixin import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Import the images builder documents name by URL into the media "
        "library, and print which asset each became."
    )
    require_tenant_scope = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be imported and write nothing.",
        )
        parser.add_argument(
            "--all-versions",
            action="store_true",
            help="Scan every stored version, not only the published ones.",
        )
        parser.add_argument(
            "--mapping-file",
            type=Path,
            help="Write the URL -> asset id map here (a real run, one tenant).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        target = options["mapping_file"]
        if target is not None and (dry_run or options["all_tenants"]):
            raise CommandError(
                "--mapping-file needs a real run for one --tenant."
            )
        for context in self.iter_tenant_contexts(options):
            with context:
                self.report(dry_run, options["all_versions"], target)

    def report(self, dry_run: bool, all_versions: bool, target: Path | None):
        plan = media_migration.scan(all_versions=all_versions)
        outcomes = media_migration.run(plan, dry_run=dry_run)
        verb = "would be" if dry_run else "was"
        for url, items in plan.internal.items():
            outcome = outcomes[url]
            result = {
                "new": f"{verb} imported",
                "existing": f"already in the library as #{outcome.asset_id}",
                "failed": f"FAILED ({outcome.detail})",
            }[outcome.status]
            if outcome.status == "new" and outcome.asset_id:
                result += f" as #{outcome.asset_id}"
            self.stdout.write(f"{url}: {result}")
            for item in items:
                self.stdout.write(
                    f"    {item.document} [{item.source}] {item.path}"
                )
        for item in plan.external:
            self.stdout.write(
                f"EXTERNAL (never fetched) {item.document} [{item.source}] "
                f"{item.path}: {item.url}"
            )
        tally = {
            status: sum(o.status == status for o in outcomes.values())
            for status in ("new", "existing", "failed")
        }
        self.stdout.write(
            f"{len(plan.internal)} image(s) of this store "
            f"({tally['new']} {verb} imported, {tally['existing']} already "
            f"in the library, {tally['failed']} failed); "
            f"{len(plan.external)} external reference(s) left alone."
        )
        if target is not None:
            target.write_text(
                json.dumps(
                    media_migration.mapping(plan, outcomes),
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.stdout.write(f"Map written to {target}.")
