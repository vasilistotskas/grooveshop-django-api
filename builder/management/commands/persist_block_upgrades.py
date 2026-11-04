"""Write the block upgrades into every stored draft and version of a store.

    uv run python manage.py persist_block_upgrades --tenant webside --dry-run
    uv run python manage.py persist_block_upgrades --all-tenants

Each upgraded document goes through ``validate_document`` against the store's
catalogue, and what that returns is what is stored, hash and references
included. One document that does not pass stops the store: nothing of it is
written, and the command fails naming every problem. ``--dry-run`` does all of
that and writes nothing. A second run finds nothing to do.

This is the one place that rewrites a ``BuilderVersion``, which is otherwise
immutable: a version keeps its number and what it renders, the storefront
having upgraded the same node on read until now. A draft keeps its revision for
the same reason, so a store is not told it has unpublished changes. The
current release's reference index is rebuilt afterwards, which is what records
the dependency rows of a page published before its blocks took data settings.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from builder import references, storefront, validation
from builder.block_upgrades import NoUpgradePath, upgrade_document
from builder.models import BuilderDocument, BuilderVersion
from builder.services import published_versions
from core.management.tenant_mixin import TenantCommandMixin
from tenant.features import feature_on


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
        moved: Counter[str] = Counter()
        drafts = versions = 0
        problems: list[str] = []
        with transaction.atomic():
            for document in BuilderDocument.objects.select_for_update():
                result = self._upgraded(
                    key=document.key,
                    data=document.draft,
                    i18n=document.draft_i18n,
                    catalogue=catalogue,
                    label=f"{document.key} draft",
                    moved=moved,
                    problems=problems,
                )
                if result is None:
                    continue
                drafts += 1
                if not dry_run:
                    BuilderDocument.objects.filter(pk=document.pk).update(
                        draft=result.data,
                        draft_i18n=result.i18n,
                        draft_catalogue_hash=catalogue.hash,
                    )
            for version in BuilderVersion.objects.select_related(
                "document"
            ).iterator():
                result = self._upgraded(
                    key=version.document.key,
                    data=version.data,
                    i18n=version.i18n,
                    catalogue=catalogue,
                    label=f"{version.document.key} version {version.number}",
                    moved=moved,
                    problems=problems,
                )
                if result is None:
                    continue
                versions += 1
                if not dry_run:
                    BuilderVersion.objects.filter(pk=version.pk).update(
                        data=result.data,
                        i18n=result.i18n,
                        block_types=result.block_types,
                        refs=result.refs,
                        catalogue_hash=catalogue.hash,
                        content_hash=result.content_hash,
                        size_bytes=result.size_bytes,
                    )
            if problems:
                transaction.set_rollback(True)
            elif not dry_run:
                references.rebuild_index(published_versions())

        verb = "would upgrade" if dry_run or problems else "upgraded"
        self.stdout.write(
            f"{verb} {drafts} draft(s), {versions} version(s): "
            f"{dict(sorted(moved.items()))}"
        )
        return [f"{connection.schema_name}: {line}" for line in problems]

    def _upgraded(
        self,
        *,
        key: str,
        data: Any,
        i18n: Any,
        catalogue: storefront.Catalogue,
        label: str,
        moved: Counter[str],
        problems: list[str],
    ) -> validation.Validated | None:
        """The document to store, or ``None`` with nothing to do or with the
        reason it cannot be upgraded added to *problems*."""
        try:
            upgraded, found = upgrade_document(data, catalogue)
        except NoUpgradePath as exc:
            problems.append(f"{label}: {exc}")
            return None
        if not found:
            return None
        moved.update(found)
        issues, result = validation.validate_document(
            key=key,
            data=upgraded,
            i18n=i18n,
            catalogue=catalogue,
            feature_on=feature_on,
        )
        if result is None:
            problems.extend(
                f"{label}: {issue.code} at {issue.path}" for issue in issues
            )
        return result
