"""Taking a store's builder data away again: only when it is an unused
import or seed, never when a person has used the builder."""

from __future__ import annotations

import pytest
from django.core.cache import cache

from builder import published, resetting
from builder.factories import (
    BuilderDocumentFactory,
    BuilderReferenceFactory,
    BuilderReleaseFactory,
    BuilderScheduledPublishFactory,
    BuilderVersionFactory,
)
from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderRelease,
    BuilderVersion,
    VersionKind,
)

pytestmark = pytest.mark.django_db


def an_import() -> BuilderRelease:
    """What an import or a seed leaves: documents at draft revision 1, one
    publish version each, and release 1 holding them."""
    versions = {}
    for key in ("template:home", "group:footer"):
        document = BuilderDocumentFactory(
            key=key,
            kind=key.partition(":")[0],
            draft_revision=1,
            published_revision=1,
        )
        version = BuilderVersionFactory(
            document=document, kind=VersionKind.PUBLISH, number=1
        )
        versions[key] = version.pk
    return BuilderReleaseFactory.create(number=1, versions=versions)


@pytest.fixture
def seeded(tenant):
    return an_import()


def counts() -> tuple[int, int, int]:
    return (
        BuilderDocument.objects.all_with_deleted().count(),
        BuilderVersion.objects.count(),
        BuilderRelease.objects.count(),
    )


class TestAnUnusedStore:
    def test_everything_is_deleted(self, seeded):
        BuilderReferenceFactory()

        report = resetting.reset_store()

        assert report.ok
        assert report.applied
        assert report.documents == sorted(seeded.versions)
        assert (report.versions, report.releases) == (len(seeded.versions), 1)
        assert counts() == (0, 0, 0)
        assert not BuilderReference.objects.exists()

    def test_a_dry_run_deletes_nothing(self, seeded):
        before = counts()

        report = resetting.reset_store(dry_run=True)

        assert report.ok
        assert not report.applied
        assert report.documents
        assert counts() == before

    def test_the_store_can_be_imported_again(self, seeded):
        resetting.reset_store()

        again = an_import()

        assert again.number == 1
        assert BuilderRelease.objects.count() == 1

    def test_cached_reads_of_the_deleted_release_are_dropped(
        self, seeded, tenant, django_capture_on_commit_callbacks
    ):
        # Release numbers restart at 1, and a cached page is keyed by the
        # number only: the next release 1 must not meet the old one's pages.
        page = published._page_cache_key(1, "template:home", "el")
        chrome = published._chrome_cache_key(1, "el")
        cache.set(page, {"stale": True})
        cache.set(chrome, {"stale": True})

        with django_capture_on_commit_callbacks(execute=True):
            resetting.reset_store()

        assert cache.get(page) is None
        assert cache.get(chrome) is None


class TestAStoreInUse:
    def refused(self) -> list[str]:
        """The codes the store was refused with."""
        before = counts()
        report = resetting.reset_store()
        assert not report.ok
        assert not report.applied
        assert counts() == before
        return [refusal.code for refusal in report.refusals]

    def test_a_later_release_refuses(self, seeded):
        BuilderRelease.objects.create(number=2, versions={})

        assert self.refused() == ["later_release"]

    def test_a_release_by_staff_refuses(self, seeded):
        BuilderRelease.objects.filter(pk=seeded.pk).update(
            published_by_staff_id=7
        )

        assert self.refused() == ["release_by_staff"]

    def test_an_edited_draft_refuses(self, seeded):
        BuilderDocument.objects.filter(key="template:home").update(
            draft_revision=2
        )

        assert self.refused() == ["edited_draft"]

    def test_a_named_save_refuses(self, seeded):
        BuilderVersion.objects.filter().update(kind=VersionKind.CHECKPOINT)

        assert self.refused() == ["named_save"]

    def test_a_version_by_staff_refuses(self, seeded):
        BuilderVersion.objects.filter().update(created_by_staff_id=7)

        assert self.refused() == ["version_by_staff"]

    def test_a_deleted_document_refuses(self, seeded):
        BuilderDocument.objects.get(key="template:home").delete()

        assert self.refused() == ["deleted_document"]

    def test_a_schedule_refuses(self, seeded):
        BuilderScheduledPublishFactory()

        assert self.refused() == ["scheduled"]


def test_a_store_with_nothing_has_nothing_to_delete(tenant):
    report = resetting.reset_store()

    assert report.ok
    assert report.documents == []
