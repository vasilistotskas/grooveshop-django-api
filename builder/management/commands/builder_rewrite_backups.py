"""The backups of the commands that rewrite stored documents in place.

    uv run python manage.py builder_rewrite_backups --tenant webside
    uv run python manage.py builder_rewrite_backups --tenant webside --restore <run> --dry-run
    uv run python manage.py builder_rewrite_backups --tenant webside --restore <run>
    uv run python manage.py builder_rewrite_backups --all-tenants --delete <run>

``persist_block_upgrades`` and ``migrate_builder_media`` print the id of their
run, and keep under it what every draft and version held before
(``BuilderRewriteBackup``). With no action this lists the runs a store holds.

``--restore`` puts one run back in one store: each draft and version that
still holds what the run stored gets its content as it was, and its backup is
deleted. A draft someone has saved since, or a version a later run rewrote, is
left as it is and named, and its backup stays. It ends with a new release of
the same versions, because the public read is cached per release. The command
that made the run can then be run again. Alt text that
``migrate_builder_media`` wrote onto an image stays: an image's alt is edited
in the library.

``--delete`` removes the backups of a run. Nothing else does: no schedule
deletes a backup, so a run can be put back until someone decides it never
will be.
"""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Min

from builder import services
from builder.document_rewrite import restore_run
from builder.models import BuilderRewriteBackup
from core.management.tenant_mixin import TenantCommandMixin
from core.retention import delete_in_batches

RELEASE_NOTE = "A rewrite of the stored documents was put back"


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "List, put back or delete what the commands that rewrite a store's "
        "builder documents in place kept of them."
    )
    require_tenant_scope = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)
        action = parser.add_mutually_exclusive_group()
        action.add_argument(
            "--restore",
            type=uuid.UUID,
            metavar="RUN",
            help="Put back what this run replaced.",
        )
        action.add_argument(
            "--delete",
            type=uuid.UUID,
            metavar="RUN",
            help="Delete the backups of this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="With --restore: do it, then roll it back.",
        )

    def handle(self, *args, **options):
        restore, delete = options["restore"], options["delete"]
        if options["dry_run"] and restore is None:
            raise CommandError("--dry-run goes with --restore.")
        for context in self.iter_tenant_contexts(options):
            with context:
                if restore is not None:
                    self._restore(restore, options["dry_run"])
                elif delete is not None:
                    deleted = delete_in_batches(
                        BuilderRewriteBackup.objects.filter(run=delete)
                    )
                    self.stdout.write(f"deleted {deleted} backup(s)")
                else:
                    self._list()

    def _list(self) -> None:
        runs = (
            BuilderRewriteBackup.objects.values("run")
            .annotate(made=Min("created_at"), rows=Count("pk"))
            .order_by("made")
        )
        for entry in runs:
            self.stdout.write(
                f"{entry['run']}  {entry['made']:%Y-%m-%d %H:%M} UTC  "
                f"{entry['rows']} backup(s)"
            )
        if not runs:
            self.stdout.write("no backups")

    def _restore(self, run: uuid.UUID, dry_run: bool) -> None:
        if not BuilderRewriteBackup.objects.filter(run=run).exists():
            self.stdout.write(f"no backup of run {run}")
            return
        with transaction.atomic():
            result = restore_run(run)
            if result.changed:
                services.release_again(RELEASE_NOTE)
            if dry_run:
                transaction.set_rollback(True)
        for line in result.left:
            self.stdout.write(line)
        self.stdout.write(
            f"{'would restore' if dry_run else 'restored'} {result.drafts} "
            f"draft(s), {result.versions} version(s); {len(result.left)} "
            "left as they are"
        )
