"""Move a store's documents from image URLs to the media library.

    uv run python manage.py migrate_builder_media --tenant webside --dry-run
    uv run python manage.py migrate_builder_media --tenant webside --decisions decisions.json
    uv run python manage.py migrate_builder_media --all-tenants

Run ``import_builder_media --all-versions`` first: this finds each image's asset
by its content and imports nothing, so an image with no asset yet is a problem
to report. For the settings the storefront's blocks declare as old
(``legacy_image_fields.json``), every draft and stored version gets the asset in
place of the URL and loses the URL and its alt, in the settings and the overlay.

The alt goes onto the image, per language, only where the image has none. Alt
text belongs to the image, so uses that disagree stop the store. Only the
drafts and the current release decide it, and a use that gives way to what the
image says is printed to read.

Anything wrong stops the store with nothing written, and is printed with every
document, version, block and setting that holds it: a value that is no image of
the store, an image the library cannot take or does not hold, uses that
disagree, a list whose empty entry cannot keep its place, a document that does
not validate. The command then exits 1. What ``import_builder_media`` cannot
bring in is for a person to decide, one value at a time, in the file
``--decisions`` names (``builder.media_migration.read_decisions``)::

    {"webside": {"images": {"https://example.org/a.png": 12,
                            "/img/old-logo.svg": "empty"},
                 "alt": {"12": {"el": "Ο πάγκος"}},
                 "slots": {"template:home draft: /sections/c/settings/images/1": "remove"}}}

An image value becomes the library image named, or no image (``"empty"``); an
image whose uses disagree gets the alt stated (an empty text leaves it
without); an empty entry of a list beside an image of the block's other list
gets a library image, or goes with its position from every list of the block
(``"remove"``). A decision about a value holds for every draft and every
version that has it, published and older ones included; a slot is decided
per draft and per version. Each decision used is printed and kept with the run.

The file is held to its word. A name in it that is no store stops the command
before anything is read; a store this invocation does not reach is skipped and
said. A decision that nothing used (a value no document holds, an alt that
needed none, a slot that is no empty entry beside an image) stops its store
like any other problem, so a file written for another day cannot pass
unnoticed, unless an earlier run of this store applied that very decision and
recorded it: then it is said and stops nothing. So when one store of several
stops, the file is fixed for that store and given again as it is.

A deleted page is moved with the rest, and its lines are marked: nobody can
open it, so what it holds is settled by a decision. Its draft is never read
again (a page brought back starts empty), so where no decision can settle it,
it is left as it is and said.

``--dry-run`` takes every step of the real run, the alt text and the release
included, in a transaction it rolls back. A second run finds nothing to do. A
version keeps its number. A draft moves to a new revision, so an editor that
has it open is answered the revision conflict on its next save and cannot
write the old settings back; it stays published if it was. The command ends
with a new release of the same versions, so the rewrite reaches visitors
through the cache like any release.

What each draft and version held is kept as a backup of the run, whose id is
printed: ``builder_rewrite_backups`` lists the runs, puts one back, and deletes
one once it is no longer wanted.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from builder import media_migration, media_services, services, storefront
from builder.document_rewrite import DELETED_PAGE, Run, rewrite_store
from builder.errors import BuilderError
from builder.models import MediaAsset
from core.management.tenant_mixin import TenantCommandMixin
from tenant.models import Tenant

COMMAND = "migrate_builder_media"
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
            help="Do everything the real run does, then roll it back.",
        )
        parser.add_argument(
            "--decisions",
            type=Path,
            help=(
                "A JSON file of what was decided, per store, for each value "
                "that has no library image and each alt its uses disagree on."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        decisions: dict[str, media_migration.Decisions] = {}
        if options["decisions"] is not None:
            try:
                decisions = media_migration.read_decisions(options["decisions"])
            except media_migration.DecisionsInvalid as exc:
                raise CommandError(f"--decisions: {exc}") from exc
        reached = {
            connection.schema_name if tenant is None else tenant.schema_name
            for tenant in self.get_target_tenants(options)
        }
        elsewhere = set(decisions) - reached
        stores = set(
            Tenant.objects.filter(schema_name__in=elsewhere).values_list(
                "schema_name", flat=True
            )
        )
        if strangers := sorted(elsewhere - stores):
            raise CommandError(
                "--decisions: "
                + ", ".join(repr(schema) for schema in strangers)
                + " is no store. Nothing was read or written."
            )
        for schema in sorted(stores):
            self.stdout.write(
                f"decisions for {schema} skipped: this run does not reach "
                "that store"
            )
        run = uuid.uuid4()
        failed: list[str] = []
        for context in self.iter_tenant_contexts(options):
            with context:
                schema = connection.schema_name
                problems = self._store(
                    run,
                    decisions.get(schema, media_migration.Decisions()),
                    dry_run,
                )
                failed += [f"{schema}: {line}" for line in problems]
        if failed:
            if any(DELETED_PAGE in line for line in failed):
                failed.append(
                    "A deleted page cannot be opened in the editor, and one "
                    "brought back starts empty: what it holds is settled in "
                    "--decisions."
                )
            raise CommandError(
                "Nothing of these stores was written. Settle each line "
                "(docs/builder.md says how) and run it again:\n"
                + "\n".join(failed)
            )

    def _store(
        self,
        run: uuid.UUID,
        decisions: media_migration.Decisions,
        dry_run: bool,
    ) -> list[str]:
        """One store, in one transaction: what stands in its way, and with
        any of it nothing written."""
        catalogue = storefront.fetch_catalogue(connection.schema_name)
        recorded = media_migration.recorded_decisions(COMMAND)

        def unused(*, alts=(), slots=()) -> list[str]:
            """The decisions nothing used that stop the store; those an
            earlier run applied are printed."""
            applied, stale = media_migration.unused_decisions(
                decisions,
                recorded,
                urls=resolution.unused,
                alts=list(alts),
                slots=list(slots),
            )
            for line in applied:
                self.stdout.write(line)
            return stale

        current, older = media_migration.stored_uses()
        resolution = media_migration.resolve(current + older, decisions)
        for line in resolution.decided:
            self.stdout.write(line)
        if resolution.problems:
            return resolution.problems + unused()
        alts = media_migration.decide_alts(
            current, resolution.assets, decisions.alts
        )
        for line in alts.gives_way + alts.decided:
            self.stdout.write(line)
        if alts.conflicts:
            return alts.conflicts + unused(alts=alts.unused)

        slots = media_migration.SlotDecisions(decisions.slots)
        with transaction.atomic():
            problems = self._fill_alts(alts.fill)
            result = rewrite_store(
                lambda data, i18n, where: media_migration.migrate_document(
                    data, i18n, where, resolution.assets, slots
                ),
                catalogue=catalogue,
                run=Run(run, COMMAND, decisions.as_stated()),
            )
            problems += result.problems
            problems += unused(alts=alts.unused, slots=slots.unused)
            if not problems and result.changed:
                services.release_again(RELEASE_NOTE)
            if problems or dry_run:
                transaction.set_rollback(True)

        for line in [*slots.used.values(), *result.left]:
            self.stdout.write(line)
        rolled_back = dry_run or bool(problems)
        self.stdout.write(
            f"{'would move' if rolled_back else 'moved'} {result.drafts} "
            f"draft(s), {result.versions} version(s), alt text on "
            f"{len(alts.fill)} image(s): {dict(sorted(result.moved.items()))}"
        )
        if result.changed and not rolled_back:
            self.stdout.write(f"backup run {run}")
        return problems

    def _fill_alts(self, fill: dict[int, dict[str, str]]) -> list[str]:
        problems: list[str] = []
        for asset in MediaAsset.objects.filter(pk__in=fill):
            try:
                media_services.fill_alt(asset, fill[asset.pk])
            except BuilderError as exc:
                problems.append(f"image {asset.pk}: {exc.detail}")
        return problems
