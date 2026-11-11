"""Write the block upgrades into every stored draft and version of a store.

    uv run python manage.py persist_block_upgrades --tenant webside --dry-run
    uv run python manage.py persist_block_upgrades --all-tenants

Each upgraded document, its translations moved with its nodes, goes through
``validate_document`` against the store's catalogue, and what that returns is
what is stored, hash and references included. One document that does not pass
stops the store: nothing of it is written, and the command fails naming every
problem. ``--dry-run`` takes every step of the real run in a transaction it
rolls back. A second run finds nothing to do. A feature switched off does not
stop a store: its block may still be on a page, which is for its next publish
to refuse and no reason for that page to stay on an old version. A feature the
platform has never heard of does.

An older storefront leaves out a node newer than it knows, so run this once
the release it follows has proved itself. What each draft and version held is
kept as a backup of the run, whose id is printed: ``builder_rewrite_backups``
puts a run back, and deletes its backups once they are no longer wanted.

This rewrites a ``BuilderVersion`` (``builder.document_rewrite``), which is
otherwise immutable: a version keeps its number and what it renders, the storefront
having upgraded the same node on read until now. A draft moves to a new
revision, so an editor that has it open is told it changed, and stays
published if it was, so a store is not told it has unpublished changes. The
current release's reference index is rebuilt afterwards, which is what records
the dependency rows of a page published before its blocks took data settings.
"""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from builder import references, storefront
from builder.block_upgrades import upgrade_document
from builder.document_rewrite import Leave, Run, rewrite_store
from builder.services import published_versions
from core.management.tenant_mixin import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Write the storefront's block upgrades into every stored draft and "
        "version of a store."
    )
    require_tenant_scope = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do everything the real run does, then roll it back.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        run = uuid.uuid4()
        failed: list[str] = []
        for context in self.iter_tenant_contexts(options):
            with context:
                failed += self._store(run, dry_run)
        if failed:
            raise CommandError(
                f"{len(failed)} document(s) cannot be upgraded; nothing of "
                "their stores was written:\n" + "\n".join(failed)
            )

    def _store(self, run: uuid.UUID, dry_run: bool) -> list[str]:
        catalogue = storefront.fetch_catalogue(connection.schema_name)
        rolled_back = dry_run
        with transaction.atomic():
            result = rewrite_store(
                lambda data, i18n, _where: upgrade_document(
                    data, i18n, catalogue
                ),
                catalogue=catalogue,
                run=Run(run, "persist_block_upgrades"),
                leave=Leave(),
            )
            if result.problems:
                rolled_back = True
            else:
                references.rebuild_index(published_versions())
            if rolled_back:
                transaction.set_rollback(True)

        for line in result.left:
            self.stdout.write(line)
        verb = "would upgrade" if rolled_back else "upgraded"
        self.stdout.write(
            f"{verb} {result.drafts} draft(s), {result.versions} version(s): "
            f"{dict(sorted(result.moved.items()))}"
        )
        if result.changed and not rolled_back:
            self.stdout.write(f"backup run {run}")
        return [f"{connection.schema_name}: {line}" for line in result.problems]
