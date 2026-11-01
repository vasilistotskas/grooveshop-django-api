"""Take a store's page builder data away again: the way back from an
import (``builder.importing``) or a seed (``builder.seeding``) that has not
been used.

It exists for one situation: the data was written, nothing reads or edits it
yet, and it has to be written again (an import that turned out wrong, a
store imported by mistake). It is not a way to undo editing, so it refuses
unless the store's builder data is exactly what an import or a seed leaves:

* one release, number 1, with no staff behind it;
* every version a publish with no staff behind it, one per document;
* every document still at draft revision 1 and none ever deleted;
* nothing scheduled.

Anything else means a person used the builder (or a later release exists),
and deleting would lose their work. The media library is not touched: it is
uploads, not import output.

What it cannot know is whether the storefront already READS the builder:
that is a storefront deployment, not state here. Resetting a store whose
storefront renders the builder takes its pages away, so the run sheet
(``docs/builder.md``) orders it before that switch only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import connection, transaction
from django.utils.translation import gettext as _

from builder import legacy, published
from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderRelease,
    BuilderScheduledPublish,
    BuilderVersion,
    VersionKind,
)
from core.cache.invalidation import mark_surface_stale
from tenant.cache import bump_cache_generation

SURFACE = "builder"


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a store is not reset: a stable ``code`` for callers and tests, a
    ``message`` for a person."""

    code: str
    message: str


@dataclass(slots=True)
class ResetReport:
    schema: str
    documents: list[str] = field(default_factory=list)
    releases: int = 0
    versions: int = 0
    refusals: list[Refusal] = field(default_factory=list)
    applied: bool = False

    @property
    def ok(self) -> bool:
        return not self.refusals


def _refusals() -> list[Refusal]:
    """Why this store's builder data is more than an unused import."""
    refusals = []
    releases = list(BuilderRelease.objects.all())
    if any(r.number != 1 for r in releases):
        refusals.append(
            Refusal(
                "later_release",
                _("There is a release after the first: someone published."),
            )
        )
    if any(r.published_by_staff_id is not None for r in releases):
        refusals.append(
            Refusal(
                "release_by_staff",
                _("A release was published by a staff member."),
            )
        )
    versions = BuilderVersion.objects.all()
    if versions.exclude(kind=VersionKind.PUBLISH).exists():
        refusals.append(
            Refusal("named_save", _("There is a named save (checkpoint)."))
        )
    if versions.exclude(created_by_staff_id=None).exists():
        refusals.append(
            Refusal(
                "version_by_staff",
                _("A version was saved by a staff member."),
            )
        )
    if versions.exclude(number=1).exists():
        refusals.append(
            Refusal("many_versions", _("A document has more than one version."))
        )
    documents = BuilderDocument.objects.all_with_deleted()
    if documents.exclude(draft_revision=1).exists():
        refusals.append(Refusal("edited_draft", _("A draft was edited.")))
    if documents.filter(is_deleted=True).exists():
        refusals.append(
            Refusal("deleted_document", _("A document was deleted."))
        )
    if BuilderScheduledPublish.objects.exists():
        refusals.append(Refusal("scheduled", _("A publication is scheduled.")))
    return refusals


def reset_store(*, dry_run: bool = False) -> ResetReport:
    """Delete the builder data of the store being served, or report why not.

    ``dry_run`` goes through everything, the deletes included, and rolls
    them back.
    """
    report = ResetReport(schema=connection.schema_name)
    report.refusals = _refusals()
    if report.refusals:
        return report

    keys = list(
        BuilderDocument.objects.all_with_deleted().values_list("key", flat=True)
    )
    numbers = list(BuilderRelease.objects.values_list("number", flat=True))
    locales = legacy.store_locales()
    with transaction.atomic():
        report.documents = sorted(keys)
        report.versions = BuilderVersion.objects.count()
        report.releases = len(numbers)
        BuilderReference.objects.all().delete()
        BuilderRelease.objects.all().delete()
        BuilderDocument.objects.all_with_deleted().hard_delete()
        if dry_run:
            transaction.set_rollback(True)
        else:
            report.applied = True

            def forget() -> None:
                published.forget_releases(numbers, keys, locales)
                bump_cache_generation(schema_name=connection.schema_name)
                mark_surface_stale(SURFACE)

            transaction.on_commit(forget)
    return report
