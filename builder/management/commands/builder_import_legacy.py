"""Import the page_config layouts and menus of each store as builder
documents and release them once (``builder.importing``).

    manage.py builder_import_legacy --tenant webside --dry-run
    manage.py builder_import_legacy --all-tenants

Per store, all or nothing, and refused when the store already has builder
data. ``--dry-run`` does everything, the writes included, and rolls them
back. The legacy tables are untouched. A store with a problem is skipped
and reported, and the command exits non-zero.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from django.core.management.base import BaseCommand, CommandError

from builder.importing import StoreReport, import_store
from core.management.tenant_mixin import TenantCommandMixin
from core.storefront import StorefrontUnavailable


class Command(TenantCommandMixin, BaseCommand):
    help = "Import legacy page layouts and menus as page builder documents."
    require_tenant_scope = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Convert, validate and write, then roll back.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print the reports as one JSON document.",
        )

    def handle(self, *args, **options):
        reports: list[StoreReport] = []
        failures = 0
        for context in self.iter_tenant_contexts(options):
            with context:
                try:
                    report = import_store(dry_run=options["dry_run"])
                except StorefrontUnavailable as exc:
                    failures += 1
                    self.stderr.write(f"storefront unavailable: {exc}")
                    continue
            reports.append(report)
            failures += not report.ok
        if options["json"]:
            self.stdout.write(
                json.dumps(
                    [asdict(report) for report in reports],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            for report in reports:
                self._print(report, dry_run=options["dry_run"])
        if failures:
            raise CommandError(f"{failures} store(s) not imported")

    def _print(self, report: StoreReport, *, dry_run: bool) -> None:
        verb = "would import" if dry_run else "imported"
        if report.ok:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{report.schema}: {verb} {len(report.documents)} "
                    f"document(s) ({len(report.published)} released"
                    + (
                        f" as release {report.release}"
                        if report.release
                        else ""
                    )
                    + f", {len(report.drafts)} draft)"
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(f"{report.schema}: not imported")
            )
        for key in report.documents:
            counts = report.counts[key]
            state = "released" if key in report.published else "draft"
            self.stdout.write(
                f"    {key} ({state}): {counts['sections']} section(s), "
                f"{counts['blocks']} block(s)"
            )
        for problem in report.problems:
            self.stdout.write(
                f"    problem {problem['document']} {problem['path']} "
                f"{problem['code']}: {problem['message']}"
            )
        for warning in report.warnings:
            self.stdout.write(
                f"    warning {warning['document']} {warning.get('path', '')} "
                f"{warning.get('code', '')}: {warning.get('message', '')}"
            )
