"""Move a store's documents from image URLs to the media library.

    uv run python manage.py migrate_builder_media --tenant webside --dry-run
    uv run python manage.py migrate_builder_media --all-tenants

Run ``import_builder_media --all-versions`` first: this finds each image's asset
by its content and imports nothing, so an image with no asset yet is a problem
to report. For the settings the storefront's blocks declare as old
(``legacy_image_fields.json``), every draft and stored version gets the asset in
place of the URL and loses the URL and its alt, in the settings and the overlay.

The alt goes onto the image, per language, only where the image has none. Alt
text belongs to the image, so uses that disagree stop the store until someone
writes it on the image, which then wins. Only the drafts and the current release
decide it, and a use that gives way to what the image says is printed to read.

Anything wrong stops the store with nothing written: a value that is no image of
the store, an image not imported, uses that disagree, a document that does not
validate. ``--dry-run`` does all of that and writes nothing. A second run finds
nothing to do. A version keeps its number and a draft its revision. The command
ends with a new release of the same versions, so the rewrite reaches visitors
through the cache like any release.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from builder import media_migration, media_services, services, storefront
from builder.document_rewrite import rewrite_store
from builder.errors import BuilderError
from builder.models import MediaAsset
from core.management.tenant_mixin import TenantCommandMixin

RELEASE_NOTE = "Images moved to the media library"


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Replace the image URLs in a store's documents with media library "
        "assets, and move their alt text onto the assets."
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
                problems = self._store(dry_run)
                failed += [
                    f"{connection.schema_name}: {line}" for line in problems
                ]
        if failed:
            raise CommandError(
                f"{len(failed)} problem(s); nothing of those stores was "
                "written:\n" + "\n".join(failed)
            )

    def _assets(
        self, uses: list[media_migration.LegacyUse]
    ) -> tuple[dict[str, int], list[str]]:
        """``{url as written: asset id}``, and what stands in the way."""
        plan, problems = media_migration.plan_of(uses)
        outcomes = media_migration.run(plan, dry_run=True)
        for url, outcome in outcomes.items():
            if outcome.status == "failed":
                problems.append(f"{url}: cannot be read ({outcome.detail})")
            elif outcome.status == "new":
                problems.append(
                    f"{url}: not in the library yet, run import_builder_media"
                )
        return media_migration.mapping(plan, outcomes), problems

    def _store(self, dry_run: bool) -> list[str]:
        catalogue = storefront.fetch_catalogue(connection.schema_name)
        current, older = media_migration.stored_uses()
        assets, problems = self._assets(current + older)
        if problems:
            return problems
        decisions = media_migration.decide_alts(current, assets)
        for line in decisions.gives_way:
            self.stdout.write(line)
        if decisions.conflicts:
            return decisions.conflicts

        with transaction.atomic():
            problems = [] if dry_run else self._fill_alts(decisions.fill)
            result = rewrite_store(
                lambda data, i18n: media_migration.migrate_document(
                    data, i18n, assets
                ),
                catalogue=catalogue,
                dry_run=dry_run,
            )
            problems += result.problems
            if problems:
                transaction.set_rollback(True)
            elif result.changed and not dry_run:
                services.release_again(RELEASE_NOTE)

        verb = "would move" if dry_run or problems else "moved"
        self.stdout.write(
            f"{verb} {result.drafts} draft(s), {result.versions} version(s), "
            f"alt text on {len(decisions.fill)} image(s): "
            f"{dict(sorted(result.moved.items()))}"
        )
        return problems

    def _fill_alts(self, fill: dict[int, dict[str, str]]) -> list[str]:
        problems: list[str] = []
        for asset in MediaAsset.objects.filter(pk__in=fill):
            try:
                media_services.fill_alt(asset, fill[asset.pk])
            except BuilderError as exc:
                problems.append(f"image {asset.pk}: {exc.detail}")
        return problems
