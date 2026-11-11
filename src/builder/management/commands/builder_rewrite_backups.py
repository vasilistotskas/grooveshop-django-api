"""The backups of the commands that rewrite stored documents in place.

    uv run python manage.py builder_rewrite_backups --tenant webside
    uv run python manage.py builder_rewrite_backups --tenant webside --restore <run> --dry-run
    uv run python manage.py builder_rewrite_backups --tenant webside --restore <run>
    uv run python manage.py builder_rewrite_backups --tenant webside --delete <run> --yes

``persist_block_upgrades`` and ``migrate_builder_media`` print the id of their
run, and keep under it what every draft and version held before
(``BuilderRewriteRun``, ``BuilderRewriteBackup``). With no action this lists
the runs a store holds: when, which command, how many backups, and what was
decided.

``--restore`` puts one run back in one store, whole or not at all. Each draft
and version gets its content as it was, and its backup is deleted. A draft
someone has saved since, or a version a later run rewrote, no longer holds
what the run stored: one such row and nothing of the store is restored, the
rows are named, and the command exits 1. ``--leave-changed-rows`` restores the
rest all the same and leaves those rows as they are, their backups kept; it
still exits 1, because the store then holds both shapes. A restored draft
moves to a new revision, like a rewritten one. A restore ends with a new
release of the same versions, because the public read is cached per release.
The command that made the run can then be run again. Alt text that
``migrate_builder_media`` wrote onto an image stays: an image's alt is edited
in the library.

``--delete`` removes a run and its backups, and with them the way back.
Nothing else does: no schedule deletes a backup. It says what it would delete
and deletes only with ``--yes``. One run id spans every store its invocation
reached, so ``--all-tenants`` needs ``--in-every-store`` said as well. The
latest run of a store is its only way back and is kept unless
``--latest-too`` is given.
"""

from __future__ import annotations

import json
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Count

from builder import services
from builder.document_rewrite import restore_run
from builder.models import BuilderRewriteBackup, BuilderRewriteRun
from core.management.tenant_mixin import TenantCommandMixin
from core.retention import delete_in_batches

RELEASE_NOTE = "A rewrite of the stored documents was put back"


def _described(record: BuilderRewriteRun, backups: int) -> str:
    return (
        f"{record.run}  {record.created_at:%Y-%m-%d %H:%M} UTC  "
        f"{record.command}  {backups} backup(s)"
    )


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
            help="Put back what this run replaced, whole or not at all.",
        )
        action.add_argument(
            "--delete",
            type=uuid.UUID,
            metavar="RUN",
            help="Delete this run and its backups (with --yes).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="With --restore: do it, then roll it back.",
        )
        parser.add_argument(
            "--leave-changed-rows",
            action="store_true",
            help=(
                "With --restore: restore the rest of a run although some of "
                "its rows changed since and are left as they are."
            ),
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="With --delete: delete what it says it would.",
        )
        parser.add_argument(
            "--latest-too",
            action="store_true",
            help="With --delete: also when it is a store's latest run.",
        )
        parser.add_argument(
            "--in-every-store",
            action="store_true",
            help="With --delete and --all-tenants: mean every store.",
        )

    def handle(self, *args, **options):
        restore, delete = options["restore"], options["delete"]
        for flag, action, belongs in (
            ("dry_run", restore, "--restore"),
            ("leave_changed_rows", restore, "--restore"),
            ("yes", delete, "--delete"),
            ("latest_too", delete, "--delete"),
            ("in_every_store", delete, "--delete"),
        ):
            if options[flag] and action is None:
                name = "--" + flag.replace("_", "-")
                raise CommandError(f"{name} goes with {belongs}.")
        if delete and options["all_tenants"] and not options["in_every_store"]:
            raise CommandError(
                "A run id spans every store its invocation reached. Name one "
                "--tenant, or say --in-every-store as well."
            )
        stopped: list[str] = []
        self._held = False
        for context in self.iter_tenant_contexts(options):
            with context:
                if restore is not None:
                    lines = self._restore(
                        restore,
                        dry_run=options["dry_run"],
                        leave_changed=options["leave_changed_rows"],
                    )
                elif delete is not None:
                    lines = self._delete(
                        delete,
                        yes=options["yes"],
                        latest_too=options["latest_too"],
                    )
                else:
                    lines = self._list()
                stopped += [
                    f"{connection.schema_name}: {line}" for line in lines
                ]
        if stopped:
            raise CommandError("\n".join(stopped))
        if (run := restore or delete) and not self._held:
            raise CommandError(
                f"No store this command reached holds run {run}. Nothing "
                "was changed."
            )

    def _list(self) -> list[str]:
        runs = BuilderRewriteRun.objects.annotate(rows=Count("backups"))
        for record in runs:
            self.stdout.write(_described(record, record.rows))
            if record.decisions:
                decided = json.dumps(
                    record.decisions, ensure_ascii=False, sort_keys=True
                )
                self.stdout.write(f"    decided: {decided}")
        if not runs:
            self.stdout.write("no backups")
        return []

    def _restore(
        self, run: uuid.UUID, *, dry_run: bool, leave_changed: bool
    ) -> list[str]:
        """What stands in the way of the restore, or was left by it."""
        if not BuilderRewriteRun.objects.filter(run=run).exists():
            self.stdout.write(f"no backup of run {run}")
            return []
        self._held = True
        with transaction.atomic():
            result = restore_run(run, leave_changed=leave_changed)
            if result.changed:
                services.release_again(RELEASE_NOTE)
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(
            f"{'would restore' if dry_run else 'restored'} {result.drafts} "
            f"draft(s), {result.versions} version(s)"
        )
        if result.gone:
            self.stdout.write(
                f"{result.gone} backup(s) of rows that no longer exist removed"
            )
        if not result.left:
            return []
        outcome = (
            "left as it is, the rest of the run restored"
            if leave_changed
            else "nothing of this store restored (--leave-changed-rows "
            "restores the rest)"
        )
        return [f"{line}; {outcome}" for line in result.left]

    def _delete(
        self, run: uuid.UUID, *, yes: bool, latest_too: bool
    ) -> list[str]:
        """Why the run was not deleted, if it was not."""
        record = (
            BuilderRewriteRun.objects.filter(run=run)
            .annotate(rows=Count("backups"))
            .first()
        )
        if record is None:
            self.stdout.write(f"no backup of run {run}")
            return []
        self._held = True
        what = _described(record, record.rows)
        latest = BuilderRewriteRun.objects.order_by(
            "-created_at", "-pk"
        ).first()
        if record == latest and not latest_too:
            kept = "is the latest run of this store and its only way back"
            return [f"{what} {kept}; --latest-too deletes it"]
        if not yes:
            return [f"{what} would be deleted; --yes deletes it"]
        deleted = delete_in_batches(
            BuilderRewriteBackup.objects.filter(run=record)
        )
        record.delete()
        self.stdout.write(f"deleted {deleted} backup(s): {what}")
        return []
