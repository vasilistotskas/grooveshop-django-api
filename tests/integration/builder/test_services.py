"""``builder.services``: the only writer of documents, versions, releases."""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied

from builder import services
from builder.errors import (
    DocumentInvalid,
    NotFoundError,
    RevisionConflict,
    StorefrontUnavailableError,
)
from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderRelease,
    BuilderVersion,
    VersionKind,
)
from product.factories.product import ProductFactory
from tenant.models import Tenant
from tests.utils.builder import document, hero, section

pytestmark = pytest.mark.django_db

HOME = "template:home"
HEADER = "group:header"


def save(user, key=HOME, *, draft=None, i18n=None, revision=0):
    return services.save_draft(
        user,
        key=key,
        draft=draft if draft is not None else document(("a", hero())),
        i18n=i18n or {},
        expected_revision=revision,
    )


def issue_codes(error: DocumentInvalid) -> set[tuple[str | None, str]]:
    return {(issue.document, issue.code) for issue in error.issues}


class TestSaveDraft:
    def test_the_first_save_creates_the_document_at_revision_one(
        self, admin, catalogue, features_on
    ):
        saved = save(admin)

        assert saved.draft_revision == 1
        assert saved.kind == "template"
        assert saved.updated_by_staff_id == admin.pk
        assert saved.draft_catalogue_hash == catalogue.hash

    def test_each_change_moves_the_revision_and_a_repeat_does_not(
        self, admin, catalogue, features_on
    ):
        first = save(admin)
        same = save(admin, revision=first.draft_revision)
        changed = save(
            admin,
            draft=document(("a", hero("Other"))),
            revision=same.draft_revision,
        )

        assert same.draft_revision == first.draft_revision == 1
        assert changed.draft_revision == 2

    def test_a_stale_revision_is_refused_with_the_current_one_and_who(
        self, admin, catalogue, features_on
    ):
        save(admin)
        save(admin, draft=document(("a", hero("v2"))), revision=1)

        with pytest.raises(RevisionConflict) as caught:
            save(admin, draft=document(("a", hero("mine"))), revision=1)

        body = caught.value.detail
        assert body["code"] == "revision_conflict"
        assert body["current_revision"] == 2
        assert body["updated_by"]["id"] == admin.pk
        assert (
            BuilderDocument.objects.get(key=HOME).draft["sections"]["a"][
                "settings"
            ]["title"]
            == "v2"
        )

    def test_creating_what_exists_is_a_conflict_and_changing_what_does_not_is_404(
        self, admin, catalogue, features_on
    ):
        save(admin)

        with pytest.raises(RevisionConflict):
            save(admin, revision=0)
        with pytest.raises(NotFoundError):
            save(
                admin,
                key="template:contact",
                draft=document(("a", section("banner@1", {"text": "x"}))),
                revision=3,
            )

    def test_an_invalid_document_is_refused_whole(
        self, admin, catalogue, features_on
    ):
        with pytest.raises(DocumentInvalid) as caught:
            save(admin, draft=document(("a", section("hero@2", {}))))

        assert caught.value.detail["code"] == "invalid_document"
        assert (
            caught.value.detail["errors"][0]["path"] == "/sections/a/settings"
        )
        assert not BuilderDocument.objects.exists()

    def test_nothing_is_saved_when_the_storefront_cannot_be_asked(
        self, admin, catalogue, features_on
    ):
        catalogue.status = 503

        with pytest.raises(StorefrontUnavailableError) as caught:
            save(admin)

        assert caught.value.detail["code"] == "storefront_unavailable"
        assert not BuilderDocument.objects.exists()

    def test_staff_cannot_change_a_document(
        self, staff, catalogue, features_on
    ):
        with pytest.raises(PermissionDenied):
            save(staff)

    def test_a_key_that_names_no_kind_is_refused(
        self, admin, catalogue, features_on
    ):
        with pytest.raises(DocumentInvalid) as caught:
            save(admin, key="whatever")

        assert issue_codes(caught.value) == {(None, "invalid_key")}

    def test_the_number_of_pages_is_limited(
        self, admin, catalogue, features_on, settings
    ):
        settings.BUILDER_MAX_PAGES = 1
        banner = document(("a", section("banner@1", {"text": "x"})))
        save(admin, key="page:one", draft=banner)

        with pytest.raises(DocumentInvalid) as caught:
            save(admin, key="page:two", draft=banner)

        assert issue_codes(caught.value) == {(None, "too_many_pages")}

    def test_a_deleted_page_can_be_created_again(
        self, admin, catalogue, features_on
    ):
        banner = document(("a", section("banner@1", {"text": "x"})))
        first = save(admin, key="page:about", draft=banner)
        services.delete_page(
            admin, key="page:about", expected_revision=first.draft_revision
        )

        again = save(admin, key="page:about", draft=banner)

        assert again.pk == first.pk
        assert again.is_deleted is False
        assert again.draft_revision > first.draft_revision


class TestCheckpoints:
    def test_a_named_save_freezes_the_draft_and_derives_its_columns(
        self, admin, catalogue, features_on
    ):
        save(admin, draft=document(("a", hero(productId=5))))

        version = services.create_checkpoint(
            admin, key=HOME, note="before sale"
        )

        assert (version.number, version.kind) == (1, VersionKind.CHECKPOINT)
        assert version.block_types == ["hero@2"]
        assert version.refs == {
            "product": {"5": ["/sections/a/settings/productId"]}
        }
        assert version.catalogue_hash == catalogue.hash
        assert version.created_by_staff_id == admin.pk
        assert version.size_bytes > 0

    def test_a_second_save_of_the_same_content_is_the_same_version(
        self, admin, catalogue, features_on
    ):
        save(admin)

        first = services.create_checkpoint(admin, key=HOME)
        second = services.create_checkpoint(admin, key=HOME)

        assert first.pk == second.pk
        assert BuilderVersion.objects.count() == 1

    def test_versions_are_immutable(self, admin, catalogue, features_on):
        save(admin)
        version = services.create_checkpoint(admin, key=HOME)
        version.note = "edited"

        with pytest.raises(ValueError, match="immutable"):
            version.save()

    def test_only_the_newest_unreleased_checkpoints_are_kept(
        self, admin, catalogue, features_on, settings
    ):
        settings.BUILDER_UNRELEASED_CHECKPOINTS_KEPT = 2
        revision = 0
        for n in range(4):
            saved = save(
                admin, draft=document(("a", hero(f"v{n}"))), revision=revision
            )
            revision = saved.draft_revision
            services.create_checkpoint(admin, key=HOME)

        kept = BuilderVersion.objects.order_by("number").values_list(
            "number", flat=True
        )
        assert list(kept) == [3, 4]

    def test_a_released_version_is_never_pruned(
        self, admin, catalogue, features_on, settings
    ):
        settings.BUILDER_UNRELEASED_CHECKPOINTS_KEPT = 1
        save(admin, draft=document(("a", hero("v0"))))
        services.create_checkpoint(admin, key=HOME)
        services.publish(admin, keys=[HOME])
        revision = 1
        for n in (1, 2, 3):
            saved = save(
                admin, draft=document(("a", hero(f"v{n}"))), revision=revision
            )
            revision = saved.draft_revision
            services.create_checkpoint(admin, key=HOME)

        numbers = set(BuilderVersion.objects.values_list("number", flat=True))
        assert numbers == {1, 4}

    def test_a_draft_that_does_not_validate_cannot_be_frozen(
        self, admin, catalogue, features_on
    ):
        BuilderDocument.objects.create(
            key=HOME, kind="template", draft=document(("a", hero("x" * 500)))
        )

        with pytest.raises(DocumentInvalid):
            services.create_checkpoint(admin, key=HOME)


class TestRestoreAndDiscard:
    def test_restoring_lands_as_a_draft_and_publishes_nothing(
        self, admin, catalogue, features_on
    ):
        first = save(admin, draft=document(("a", hero("one"))))
        services.create_checkpoint(admin, key=HOME)
        save(
            admin,
            draft=document(("a", hero("two"))),
            revision=first.draft_revision,
        )

        restored = services.restore_version(
            admin, key=HOME, number=1, expected_revision=2
        )

        assert restored.draft["sections"]["a"]["settings"]["title"] == "one"
        assert restored.draft_revision == 3
        assert not BuilderRelease.objects.exists()

    def test_restoring_records_a_version_naming_its_source(
        self, admin, catalogue, features_on
    ):
        first = save(admin, draft=document(("a", hero("one"))))
        services.create_checkpoint(admin, key=HOME)
        second = save(
            admin,
            draft=document(("a", hero("two"))),
            revision=first.draft_revision,
        )
        services.create_checkpoint(admin, key=HOME)

        services.restore_version(
            admin, key=HOME, number=1, expected_revision=second.draft_revision
        )

        newest = BuilderVersion.objects.get(document__key=HOME, number=3)
        assert newest.kind == VersionKind.RESTORE
        assert newest.restored_from == 1
        assert newest.content_hash == (
            BuilderVersion.objects.get(
                document__key=HOME, number=1
            ).content_hash
        )
        assert (
            BuilderVersion.objects.get(
                document__key=HOME, number=2
            ).restored_from
            is None
        )

    def test_restoring_what_the_newest_version_holds_adds_nothing(
        self, admin, catalogue, features_on
    ):
        first = save(admin, draft=document(("a", hero("one"))))
        services.create_checkpoint(admin, key=HOME)

        services.restore_version(
            admin, key=HOME, number=1, expected_revision=first.draft_revision
        )

        assert BuilderVersion.objects.filter(document__key=HOME).count() == 1

    def test_restore_checks_the_revision_and_the_version(
        self, admin, catalogue, features_on
    ):
        save(admin)

        with pytest.raises(RevisionConflict):
            services.restore_version(
                admin, key=HOME, number=1, expected_revision=9
            )
        with pytest.raises(NotFoundError):
            services.restore_version(
                admin, key=HOME, number=9, expected_revision=1
            )

    def test_discarding_returns_the_published_content(
        self, admin, catalogue, features_on
    ):
        save(admin, draft=document(("a", hero("live"))))
        services.publish(admin, keys=[HOME])
        save(admin, draft=document(("a", hero("wip"))), revision=1)

        discarded = services.discard_draft(admin, key=HOME, expected_revision=2)

        assert discarded.draft["sections"]["a"]["settings"]["title"] == "live"
        assert discarded.draft_revision == 3
        assert discarded.published_revision == 3

    def test_discarding_what_was_never_published_empties_the_draft(
        self, admin, catalogue, features_on
    ):
        save(admin)

        discarded = services.discard_draft(admin, key=HOME, expected_revision=1)

        assert discarded.draft == {"sections": {}, "order": []}


class TestPublish:
    def test_several_documents_are_one_release(
        self, admin, catalogue, features_on
    ):
        save(admin, HOME)
        save(
            admin,
            HEADER,
            draft=document(("n", section("announcement@1", {"text": "Sale"}))),
        )

        result = services.publish(admin, keys=[HOME, HEADER], note="launch")

        assert BuilderRelease.objects.count() == 1
        release = result.release
        assert release.number == 1
        assert release.note == "launch"
        assert release.published_by_staff_id == admin.pk
        assert set(release.versions) == {HOME, HEADER}
        assert {v.kind for v in BuilderVersion.objects.all()} == {
            VersionKind.PUBLISH
        }
        assert BuilderDocument.objects.get(key=HOME).published_revision == 1

    def test_one_bad_document_stops_the_whole_publish(
        self, admin, catalogue, features_on
    ):
        save(admin, HOME)
        BuilderDocument.objects.create(
            key=HEADER,
            kind="group",
            draft=document(("n", section("announcement@1", {}))),
        )

        with pytest.raises(DocumentInvalid) as caught:
            services.publish(admin, keys=[HOME, HEADER])

        assert issue_codes(caught.value) == {(HEADER, "invalid_settings")}
        assert not BuilderRelease.objects.exists()
        assert not BuilderVersion.objects.exists()

    def test_a_release_keeps_what_it_does_not_name(
        self, admin, catalogue, features_on
    ):
        save(admin, HOME)
        save(
            admin,
            HEADER,
            draft=document(("n", section("announcement@1", {"text": "Sale"}))),
        )
        services.publish(admin, keys=[HOME, HEADER])
        save(admin, HOME, draft=document(("a", hero("new"))), revision=1)

        second = services.publish(admin, keys=[HOME]).release

        first = BuilderRelease.objects.get(number=1)
        assert second.number == 2
        assert second.versions[HEADER] == first.versions[HEADER]
        assert second.versions[HOME] != first.versions[HOME]

    def test_publishing_what_has_not_changed_is_refused(
        self, admin, catalogue, features_on
    ):
        save(admin, HOME)
        services.publish(admin, keys=[HOME])

        with pytest.raises(DocumentInvalid) as caught:
            services.publish(admin, keys=[HOME])

        assert issue_codes(caught.value) == {(None, "nothing_to_publish")}

    def test_nothing_named_nothing_released(
        self, admin, catalogue, features_on
    ):
        with pytest.raises(DocumentInvalid):
            services.publish(admin, keys=[])
        with pytest.raises(NotFoundError):
            services.publish(admin, keys=["template:missing"])

    def test_staff_cannot_publish(self, admin, staff, catalogue, features_on):
        save(admin, HOME)

        with pytest.raises(PermissionDenied):
            services.publish(staff, keys=[HOME])

    def test_references_must_exist_to_be_published(
        self, admin, catalogue, features_on
    ):
        save(admin, HOME, draft=document(("a", hero(productId=987654))))

        with pytest.raises(DocumentInvalid) as caught:
            services.publish(admin, keys=[HOME])

        errors = caught.value.detail["errors"]
        assert errors == [
            {
                "path": "/sections/a/settings/productId",
                "code": "reference_not_found",
                "message": errors[0]["message"],
                "document": HOME,
                "params": {"kind": "product", "target": "987654"},
            }
        ]

    def test_a_draft_may_point_at_what_does_not_exist_yet(
        self, admin, catalogue, features_on
    ):
        saved = save(admin, HOME, draft=document(("a", hero(productId=987654))))

        assert saved.draft_revision == 1

    def test_the_reference_index_follows_the_current_release(
        self, admin, catalogue, features_on
    ):
        product = ProductFactory(active=True)
        save(admin, HOME, draft=document(("a", hero(productId=product.pk))))

        services.publish(admin, keys=[HOME])

        row = BuilderReference.objects.get()
        assert (row.kind, row.target, row.document, row.node_path) == (
            "product",
            str(product.pk),
            HOME,
            "/sections/a/settings/productId",
        )

        save(admin, HOME, draft=document(("a", hero())), revision=1)
        services.publish(admin, keys=[HOME])
        assert not BuilderReference.objects.exists()

    def test_a_page_reference_is_a_page_the_store_built(
        self, admin, catalogue, features_on
    ):
        banner = document(("b", section("banner@1", {"text": "x"})))
        save(admin, "page:about", draft=banner)
        save(admin, HOME, draft=document(("a", hero(pageSlug="about"))))
        save(
            admin,
            "template:contact",
            draft=document(("a", section("banner@1", {"text": "x"}))),
        )

        services.publish(admin, keys=[HOME, "page:about"])

        assert BuilderReference.objects.get().target == "about"

    def test_a_publish_moves_the_stores_cache_generation(
        self, admin, tenant, catalogue, features_on
    ):
        save(admin, HOME)
        before = Tenant.objects.get(pk=tenant.pk).cache_generation

        services.publish(admin, keys=[HOME])

        assert Tenant.objects.get(pk=tenant.pk).cache_generation > before

    def test_a_draft_save_moves_nothing(
        self, admin, tenant, catalogue, features_on
    ):
        before = Tenant.objects.get(pk=tenant.pk).cache_generation

        save(admin, HOME)

        assert Tenant.objects.get(pk=tenant.pk).cache_generation == before


class TestRollback:
    def test_a_rollback_is_a_new_release_pointing_at_old_versions(
        self, admin, catalogue, features_on
    ):
        save(admin, HOME, draft=document(("a", hero("one"))))
        first = services.publish(admin, keys=[HOME]).release
        save(admin, HOME, draft=document(("a", hero("two"))), revision=1)
        services.publish(admin, keys=[HOME])

        rolled = services.rollback(admin, release_id=first.pk, note="oops")

        assert rolled.number == 3
        assert rolled.rolled_back_from == first
        assert rolled.versions == first.versions
        assert BuilderRelease.objects.count() == 3
        assert BuilderDocument.objects.get(key=HOME).published_revision is None

    def test_rolling_back_to_the_current_release_is_refused(
        self, admin, catalogue, features_on
    ):
        save(admin, HOME)
        current = services.publish(admin, keys=[HOME]).release

        with pytest.raises(DocumentInvalid):
            services.rollback(admin, release_id=current.pk)
        with pytest.raises(NotFoundError):
            services.rollback(admin, release_id=999999)

    def test_a_rollback_rebuilds_the_reference_index(
        self, admin, catalogue, features_on
    ):
        product = ProductFactory(active=True)
        save(admin, HOME, draft=document(("a", hero(productId=product.pk))))
        first = services.publish(admin, keys=[HOME]).release
        save(admin, HOME, draft=document(("a", hero())), revision=1)
        services.publish(admin, keys=[HOME])
        assert not BuilderReference.objects.exists()

        services.rollback(admin, release_id=first.pk)

        assert BuilderReference.objects.count() == 1


class TestDeletePage:
    banner = document(("b", section("banner@1", {"text": "x"})))

    def test_a_released_page_leaves_the_store_with_a_new_release(
        self, admin, catalogue, features_on
    ):
        save(admin, "page:about", draft=self.banner)
        services.publish(admin, keys=["page:about"])

        services.delete_page(admin, key="page:about", expected_revision=1)

        assert not BuilderDocument.objects.filter(key="page:about").exists()
        assert services.published_versions() == {}
        assert BuilderRelease.objects.count() == 2

    def test_a_page_that_another_released_document_links_to_stays(
        self, admin, catalogue, features_on
    ):
        save(admin, "page:about", draft=self.banner)
        save(admin, HOME, draft=document(("a", hero(pageSlug="about"))))
        services.publish(admin, keys=[HOME, "page:about"])

        with pytest.raises(DocumentInvalid) as caught:
            services.delete_page(admin, key="page:about", expected_revision=1)

        assert issue_codes(caught.value) == {(None, "page_in_use")}

    def test_only_pages_are_deleted(self, admin, catalogue, features_on):
        save(admin, HOME)

        with pytest.raises(DocumentInvalid) as caught:
            services.delete_page(admin, key=HOME, expected_revision=1)

        assert issue_codes(caught.value) == {(None, "not_a_page")}


class TestBlogCategoryReference:
    def test_it_must_exist_to_be_published(self, admin, catalogue, features_on):
        from blog.factories.category import BlogCategoryFactory

        category = BlogCategoryFactory()
        save(
            admin, HOME, draft=document(("a", hero(blogCategoryId=category.pk)))
        )
        services.publish(admin, keys=[HOME])
        assert BuilderReference.objects.get().kind == "blogCategory"

        save(
            admin,
            HOME,
            draft=document(("a", hero(blogCategoryId=987654))),
            revision=1,
        )
        with pytest.raises(DocumentInvalid) as caught:
            services.publish(admin, keys=[HOME])

        assert issue_codes(caught.value) == {(HOME, "reference_not_found")}


class TestLinkReferences:
    """A link that is ``{ref: {kind, id}}`` is a reference like any other:
    indexed, and refused at publish when its target is gone."""

    def link(self, kind, ident):
        return {"link": {"ref": {"kind": kind, "id": ident}}}

    @pytest.fixture
    def targets(self, db):
        from blog.factories.category import BlogCategoryFactory
        from blog.factories.post import BlogPostFactory
        from page_config.models import ContentPage
        from product.factories.category import ProductCategoryFactory

        ContentPage.objects.create(slug="privacy-policy", is_published=False)
        return {
            "product": ProductFactory(active=True).pk,
            "category": ProductCategoryFactory(active=True).pk,
            "blogCategory": BlogCategoryFactory().pk,
            "blogPost": BlogPostFactory(is_published=True).pk,
            "contentPage": "privacy-policy",
        }

    @pytest.mark.parametrize(
        "kind",
        ["product", "category", "blogCategory", "blogPost", "contentPage"],
    )
    def test_it_is_indexed_and_must_exist_to_be_published(
        self, admin, catalogue, features_on, targets, kind
    ):
        save(
            admin,
            HOME,
            draft=document(("a", hero(**self.link(kind, targets[kind])))),
        )
        services.publish(admin, keys=[HOME])
        row = BuilderReference.objects.get()
        assert (row.kind, row.target, row.node_path) == (
            kind,
            str(targets[kind]),
            "/sections/a/settings/link",
        )

        gone = "no-such-page" if kind == "contentPage" else 987654
        save(
            admin,
            HOME,
            draft=document(("a", hero(**self.link(kind, gone)))),
            revision=1,
        )
        with pytest.raises(DocumentInvalid) as caught:
            services.publish(admin, keys=[HOME])

        assert issue_codes(caught.value) == {(HOME, "reference_not_found")}

    def test_a_page_link_names_a_page_or_template_document(
        self, admin, catalogue, features_on
    ):
        banner = document(("b", section("banner@1", {"text": "x"})))
        save(admin, "page:kelari", draft=banner)
        save(
            admin,
            HOME,
            draft=document(("a", hero(**self.link("page", "kelari")))),
        )

        services.publish(admin, keys=[HOME])

        row = BuilderReference.objects.get()
        assert (row.kind, row.target) == ("page", "kelari")

    def test_a_link_inside_a_list_is_indexed_at_its_own_pointer(
        self, admin, catalogue, features_on, targets
    ):
        cards = [
            self.link("product", targets["product"]),
            {"link": {"href": "https://example.com"}},
            self.link("blogPost", targets["blogPost"]),
        ]
        save(admin, HOME, draft=document(("a", hero(cards=cards))))

        services.publish(admin, keys=[HOME])

        assert {
            (r.kind, r.node_path) for r in BuilderReference.objects.all()
        } == {
            ("product", "/sections/a/settings/cards/0/link"),
            ("blogPost", "/sections/a/settings/cards/2/link"),
        }

    def test_an_href_and_a_route_point_at_nothing_of_ours(
        self, admin, catalogue, features_on
    ):
        save(
            admin,
            HOME,
            draft=document(
                ("a", hero(link={"href": "https://example.com/x"})),
                ("b", hero(link={"route": "/offers"})),
            ),
        )

        services.publish(admin, keys=[HOME])

        assert not BuilderReference.objects.exists()


class TestPageReferences:
    def test_a_page_is_named_by_the_key_of_a_page_or_a_template(
        self, admin, catalogue, features_on
    ):
        banner = document(("b", section("banner@1", {"text": "x"})))
        save(admin, "template:about", draft=banner)
        save(admin, HOME, draft=document(("a", hero(pageSlug="about"))))

        services.publish(admin, keys=[HOME])

        assert BuilderReference.objects.get().target == "about"

    def test_a_page_that_has_no_document_is_refused(
        self, admin, catalogue, features_on
    ):
        save(admin, HOME, draft=document(("a", hero(pageSlug="nowhere"))))

        with pytest.raises(DocumentInvalid) as caught:
            services.publish(admin, keys=[HOME])

        assert issue_codes(caught.value) == {(HOME, "reference_not_found")}

    def test_a_content_page_exists_in_any_state(
        self, admin, catalogue, features_on
    ):
        from page_config.models import ContentPage

        ContentPage.objects.create(slug="privacy-policy", is_published=False)
        save(
            admin,
            HOME,
            draft=document(("a", hero(contentSlug="privacy-policy"))),
        )
        services.publish(admin, keys=[HOME])
        assert BuilderReference.objects.get().kind == "contentPage"

        save(
            admin,
            HOME,
            draft=document(("a", hero(contentSlug="no-such-page"))),
            revision=1,
        )
        with pytest.raises(DocumentInvalid) as caught:
            services.publish(admin, keys=[HOME])

        assert issue_codes(caught.value) == {(HOME, "reference_not_found")}
