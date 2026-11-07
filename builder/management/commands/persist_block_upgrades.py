"""Write the block upgrades into every stored draft and version of a store.

    uv run python manage.py persist_block_upgrades --tenant webside --dry-run
    uv run python manage.py persist_block_upgrades --all-tenants

Each upgraded document, its translations moved with its nodes, goes through
``validate_document`` against the store's catalogue, and what that returns is
what is stored, hash and references included. One document that does not pass
stops the store: nothing of it is written, and the command fails naming every
problem. ``--dry-run`` does all of that and writes nothing. A second run finds
nothing to do. A feature switched off does not stop a store: its block may still
be on a page, which is for its next publish to refuse and no reason for that
page to stay on an old version. A feature the platform has never heard of does.

There is no way back. An older storefront leaves out a node newer than it knows,
so run this once the release it follows has proved itself, and only with a
restorable backup of the store.

This is the one place that rewrites a ``BuilderVersion``, which is otherwise
immutable: a version keeps its number and what it renders, the storefront
having upgraded the same node on read until now. A draft keeps its revision for
the same reason, so a store is not told it has unpublished changes. The
current release's reference index is rebuilt afterwards, which is what records
the dependency rows of a page published before its blocks took data settings.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from builder import references, storefront
from builder.block_upgrades import upgrade_document
from builder.document_rewrite import rewrite_store
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
            help="Report what would change and write nothing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        failed: list[str] = []
        for context in self.iter_tenant_contexts(options):
            with context:
                failed += self._store(dry_run)
        if failed:
            raise CommandError(
                f"{len(failed)} document(s) cannot be upgraded; nothing of "
                "their stores was written:\n" + "\n".join(failed)
            )

    def _store(self, dry_run: bool) -> list[str]:
        catalogue = storefront.fetch_catalogue(connection.schema_name)
        with transaction.atomic():
            result = rewrite_store(
                lambda data, i18n: upgrade_document(data, i18n, catalogue),
                catalogue=catalogue,
                dry_run=dry_run,
            )
            if result.problems:
                transaction.set_rollback(True)
            elif not dry_run:
                references.rebuild_index(published_versions())

        verb = "would upgrade" if dry_run or result.problems else "upgraded"
        self.stdout.write(
            f"{verb} {result.drafts} draft(s), {result.versions} version(s): "
            f"{dict(sorted(result.moved.items()))}"
        )
        return [f"{connection.schema_name}: {line}" for line in result.problems]
