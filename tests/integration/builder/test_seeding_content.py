"""``builder.seeding.seed_with_content``: content released in one release
over a store with nothing, or with only the untouched defaults, validated
like a publish, and never over anything an editor did."""

from __future__ import annotations

import pytest

from builder import seeding, services
from builder.errors import DocumentInvalid
from builder.models import BuilderDocument, BuilderReference, BuilderRelease
from product.factories.product import ProductFactory
from tests.utils.builder import document, hero, section

HOME = "template:home"
ABOUT = "page:about"
NOTE = "Demo content"


def _seed(key: str, data: dict, i18n: dict | None = None) -> seeding.Seed:
    return seeding.Seed(key=key, data=data, i18n=i18n or {})


def _about() -> seeding.Seed:
    # ``columns`` names no template, so a page takes it; ``hero`` is home's.
    return _seed(ABOUT, document(("c", section("columns@1"))))


@pytest.mark.django_db
class TestSeedWithContent:
    def test_an_empty_store_gets_one_release_of_the_content(
        self, tenant, catalogue, features_on
    ):
        data = document(("a", hero()))

        release = seeding.seed_with_content([_seed(HOME, data)], note=NOTE)

        assert release is not None
        assert release.number == 1
        assert release.note == NOTE
        assert release.published_by_staff_id is None
        assert set(release.versions) == {HOME}
        home = BuilderDocument.objects.get(key=HOME)
        assert home.draft == data
        assert home.draft_revision == home.published_revision == 1

    def test_untouched_defaults_are_replaced_where_named_and_kept_elsewhere(
        self, tenant, catalogue, features_on
    ):
        defaults = seeding.seed_store()
        assert defaults is not None

        release = seeding.seed_with_content([_about()], note=NOTE)

        assert release is not None
        assert release.number == 2
        assert release.versions[HOME] == defaults.versions[HOME]
        assert set(release.versions) == {HOME, ABOUT}

    def test_a_named_default_gets_a_new_version_and_revision(
        self, tenant, catalogue, features_on
    ):
        defaults = seeding.seed_store()
        assert defaults is not None

        release = seeding.seed_with_content(
            [_seed(HOME, document(("a", hero())))], note=NOTE
        )

        assert release is not None
        assert release.versions[HOME] != defaults.versions[HOME]
        home = BuilderDocument.objects.get(key=HOME)
        assert home.draft_revision == home.published_revision == 2
        assert home.versions.count() == 2

    def test_a_store_an_editor_touched_is_left_alone(
        self, tenant, catalogue, features_on, admin
    ):
        seeding.seed_store()
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", hero(title="Mine"))),
            i18n={},
            expected_revision=1,
        )

        assert seeding.seed_with_content([_about()], note=NOTE) is None

        assert BuilderRelease.objects.count() == 1
        assert not BuilderDocument.objects.filter(key=ABOUT).exists()

    def test_a_store_with_only_a_draft_is_left_alone(
        self, tenant, catalogue, features_on, admin
    ):
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", hero())),
            i18n={},
            expected_revision=0,
        )

        assert seeding.seed_with_content([_about()], note=NOTE) is None
        assert not BuilderRelease.objects.exists()

    def test_invalid_content_writes_nothing(
        self, tenant, catalogue, features_on
    ):
        with pytest.raises(DocumentInvalid) as caught:
            seeding.seed_with_content(
                [_about(), _seed(HOME, document(("a", section("nope@1"))))],
                note=NOTE,
            )

        assert {issue.document for issue in caught.value.issues} == {HOME}
        assert not BuilderDocument.objects.exists()
        assert not BuilderRelease.objects.exists()

    def test_a_reference_to_nothing_writes_nothing(
        self, tenant, catalogue, features_on
    ):
        with pytest.raises(DocumentInvalid) as caught:
            seeding.seed_with_content(
                [_seed(HOME, document(("a", hero(productId=987654))))],
                note=NOTE,
            )

        assert [issue.code for issue in caught.value.issues] == [
            "reference_not_found"
        ]
        assert not BuilderDocument.objects.exists()
        assert not BuilderRelease.objects.exists()

    def test_references_are_indexed_like_a_publishs(
        self, tenant, catalogue, features_on
    ):
        product = ProductFactory(active=True)

        seeding.seed_with_content(
            [_seed(HOME, document(("a", hero(productId=product.pk))))],
            note=NOTE,
        )

        assert BuilderReference.objects.filter(
            kind="product", target=str(product.pk)
        ).exists()

    def test_a_seed_may_point_at_another_seed(
        self, tenant, catalogue, features_on
    ):
        release = seeding.seed_with_content(
            [
                _seed(HOME, document(("a", hero(pageSlug="about")))),
                _about(),
            ],
            note=NOTE,
        )

        assert release is not None
        assert set(release.versions) == {HOME, ABOUT}
