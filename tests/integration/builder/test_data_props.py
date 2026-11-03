"""A block's typed data props: validated, and indexed as references and as
query dependencies."""

from __future__ import annotations

import pytest

from builder import services
from builder.errors import DocumentInvalid
from builder.models import BuilderReference, ReferenceKind
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from tests.utils.builder import document, section

pytestmark = pytest.mark.django_db

HOME = "template:home"
RAIL = {
    "field": "query",
    "source": "product",
    "onEmpty": "hide",
    "maxLimit": 12,
}
PICK = {"field": "ref", "kind": "product", "onMissing": "empty"}


@pytest.fixture
def rails(catalogue):
    """The hero block also takes a product rail and a picked product."""
    hero = next(b for b in catalogue.blocks if b["type"] == "hero")
    hero["schema"]["properties"]["rail"] = {"type": "object"}
    hero["schema"]["properties"]["pick"] = {"type": "integer"}
    hero["data"] = {"/rail": RAIL, "/pick": PICK}
    return catalogue


def hero_with(**settings_):
    return section("hero@2", {"title": "Welcome", **settings_})


def save(user, *sections):
    return services.save_draft(
        user,
        key=HOME,
        draft=document(*sections),
        i18n={},
        expected_revision=0,
    )


def issues_of(error: DocumentInvalid) -> set[tuple[str, str]]:
    return {(i.path, i.code) for i in error.issues}


class TestValidation:
    def test_a_rule_that_asks_for_a_filter_the_list_does_not_have(
        self, admin, rails, features_on
    ):
        rail = {
            "mode": "filter",
            "ordering": "newest",
            "limit": 4,
            "minPrice": 1,
        }

        with pytest.raises(DocumentInvalid) as caught:
            save(admin, ("a", hero_with(rail=rail)))

        assert issues_of(caught.value) == {
            ("/sections/a/settings/rail/minPrice", "unknown_filter")
        }

    def test_a_rule_over_the_fields_own_limit(self, admin, rails, features_on):
        rail = {"mode": "filter", "ordering": "newest", "limit": 13}

        with pytest.raises(DocumentInvalid) as caught:
            save(admin, ("a", hero_with(rail=rail)))

        assert issues_of(caught.value) == {
            ("/sections/a/settings/rail/limit", "invalid_data_value")
        }

    def test_a_picked_product_must_be_a_number(self, admin, rails, features_on):
        with pytest.raises(DocumentInvalid) as caught:
            save(admin, ("a", hero_with(pick=0)))

        assert ("/sections/a/settings/pick", "invalid_data_value") in issues_of(
            caught.value
        )

    def test_a_document_may_ask_for_only_so_many_lists(
        self, admin, rails, features_on, settings
    ):
        settings.BUILDER_DATA_MAX_QUERIES = 1
        rail = {"mode": "filter", "ordering": "newest", "limit": 4}

        with pytest.raises(DocumentInvalid) as caught:
            save(
                admin, ("a", hero_with(rail=rail)), ("b", hero_with(rail=rail))
            )

        assert ("", "too_many_queries") in issues_of(caught.value)


class TestWhatItIndexes:
    def test_the_records_named_and_the_lists_asked_for(
        self, admin, rails, features_on
    ):
        category = ProductCategoryFactory()
        product = ProductFactory(active=True)
        rail = {
            "mode": "filter",
            "categories": [category.pk],
            "ordering": "newest",
            "limit": 4,
        }
        saved = save(
            admin,
            ("a", hero_with(rail=rail, pick=product.pk)),
        )
        services.publish(admin, keys=[HOME])

        rows = {
            (r.kind, r.target, r.node_path)
            for r in BuilderReference.objects.all()
        }
        assert rows == {
            (
                ReferenceKind.CATEGORY,
                str(category.pk),
                "/sections/a/settings/rail",
            ),
            (
                ReferenceKind.QUERY_PRODUCT,
                f"category:{category.pk}",
                "/sections/a/settings/rail",
            ),
            (
                ReferenceKind.PRODUCT,
                str(product.pk),
                "/sections/a/settings/pick",
            ),
        }
        assert saved.draft_revision == 1

    def test_a_rule_without_a_filter_depends_on_every_product(
        self, admin, rails, features_on
    ):
        rail = {"mode": "filter", "ordering": "mostViewed", "limit": 4}
        save(admin, ("a", hero_with(rail=rail)))
        services.publish(admin, keys=[HOME])

        row = BuilderReference.objects.get()

        assert (row.kind, row.target) == (ReferenceKind.QUERY_PRODUCT, "*")

    def test_a_missing_record_is_refused_at_publish_but_a_facet_is_not_checked(
        self, admin, rails, features_on
    ):
        rail = {"mode": "filter", "ordering": "newest", "limit": 4}
        save(admin, ("a", hero_with(rail=rail, pick=987654)))

        with pytest.raises(DocumentInvalid) as caught:
            services.publish(admin, keys=[HOME])

        assert {i.code for i in caught.value.issues} == {"reference_not_found"}
        assert {i.params["kind"] for i in caught.value.issues if i.params} == {
            "product"
        }
