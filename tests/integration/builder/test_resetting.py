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


def an_import(
    text: str = "x", keys: tuple[str, ...] = ("template:home", "group:footer")
) -> BuilderRelease:
    """What an import or a seed leaves: documents at draft revision 1, one
    publish version each, and release 1 holding them."""
    versions = {}
    for key in keys:
        document = BuilderDocumentFactory(
            key=key,
            kind=key.partition(":")[0],
            draft_revision=1,
            published_revision=1,
        )
        version = BuilderVersionFactory(
            document=document,
            kind=VersionKind.PUBLISH,
            number=1,
            data={
                "sections": {
                    "a": {"type": "banner@1", "settings": {"text": text}}
                },
                "order": ["a"],
            },
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


class TestTheReadAfterAReimport:
    """Release numbers restart at 1 after a reset; what the public reads
    cache is keyed by the release's primary key, which never comes back, so
    a re-import is never served the old release's content and a reset has
    no cache key to forget."""

    def test_a_new_release_one_is_not_served_from_the_old_ones_cache(
        self, tenant
    ):
        cache.clear()
        an_import("old")
        # The bound tenant is read once per request in production; here it
        # is one object, so it is reloaded where a request would start.
        tenant.refresh_from_db()
        first = published.published_page("home", "el")
        assert first is not None
        assert first["page"]["sections"]["a"]["settings"]["text"] == "old"
        assert [p["key"] for p in published.published_index("el")["pages"]] == [
            "home"
        ]

        resetting.reset_store()
        an_import("new", ("template:home", "page:extra", "group:footer"))
        tenant.refresh_from_db()
        again = published.published_page("home", "el")
        index = published.published_index("el")

        assert again is not None
        assert again["release"] == 1
        assert again["page"]["sections"]["a"]["settings"]["text"] == "new"
        assert [p["key"] for p in index["pages"]] == ["extra", "home"]

    def test_the_two_releases_number_one_have_different_cache_keys(
        self, tenant
    ):
        first = an_import()
        old_key = published._page_cache_key(first.pk, "home", "el")
        resetting.reset_store()

        second = an_import()

        assert second.number == first.number == 1
        assert second.pk != first.pk
        assert published._page_cache_key(second.pk, "home", "el") != old_key


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
