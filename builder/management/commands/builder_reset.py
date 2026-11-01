"""Delete a store's page builder data again, when nobody has used it yet
(``builder.resetting``): the way back from ``builder_import_legacy``.

    manage.py builder_reset --tenant webside            # report only
    manage.py builder_reset --tenant webside --confirm  # delete

Without ``--confirm`` it reports what it would delete and deletes nothing.
A store whose builder data is more than an unused import or seed (a second
release, an edited draft, a named save, a schedule) is refused and reported,
and the command exits non-zero. One store at a time: there is no
``--all-tenants``, so a reset is always a decision about one store. The
legacy tables are untouched.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from builder.resetting import reset_store
from core.management.tenant_mixin import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = "Delete the page builder data of one store, if it is unused."
    require_tenant_scope = True

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            required=True,
            help="The store's schema name.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Delete. Without it, only report.",
        )

    def handle(self, *args, **options):
        for context in self.iter_tenant_contexts(options):
            with context:
                report = reset_store(dry_run=not options["confirm"])
        if not report.ok:
            for reason in report.refusals:
                self.stderr.write(f"{report.schema}: refused: {reason}")
            raise CommandError(f"{report.schema}: builder data is in use")
        verb = "deleted" if report.applied else "would delete"
        self.stdout.write(
            self.style.SUCCESS(
                f"{report.schema}: {verb} {len(report.documents)} "
                f"document(s), {report.versions} version(s), "
                f"{report.releases} release(s)"
            )
        )
        for key in report.documents:
            self.stdout.write(f"    {key}")
        if not report.applied:
            self.stdout.write("Nothing deleted: pass --confirm to delete.")
