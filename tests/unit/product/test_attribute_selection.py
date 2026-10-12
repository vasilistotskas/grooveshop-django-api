"""AttributeSelection: the one reading of "selected attribute values".

Within one attribute the values are alternatives (OR), across attributes
every selection narrows (AND). The database side is checked against real
rows; the Meilisearch side only builds a filter list, so it needs no
engine (the live behaviour is in
``tests/integration/search/test_product_search_attribute_filters.py``).
"""

import pytest

from meili.querysets import FacetGroup
from product.attribute_selection import (
    ATTRIBUTE_VALUES_FIELD,
    AttributeSelection,
)
from product.factories.product import ProductFactory
from product.factories.product_attribute import ProductAttributeFactory
from product.models.product import Product, ProductTranslation
from tests.utils.attributes import GRID, assign_values, create_values

UNKNOWN_VALUE_ID = 2**40


@pytest.fixture
def values(db):
    return create_values()


@pytest.fixture
def products(values):
    created = {}
    for key in GRID:
        product = ProductFactory(active=True, num_reviews=0)
        assign_values(product, key, values)
        created[key] = product
    return created


def _select(values, *keys, extra=()):
    return AttributeSelection.from_value_ids(
        [values[key].id for key in keys] + list(extra)
    )


def _kept(selection, products):
    kept = set(
        selection.filter_products(
            Product.objects.filter(pk__in=[p.pk for p in products.values()])
        ).values_list("pk", flat=True)
    )
    return {key for key, p in products.items() if p.pk in kept}


@pytest.mark.django_db
class TestGrouping:
    def test_groups_the_selected_ids_by_attribute(self, values):
        selection = _select(values, "white", "black", "two_m")

        assert selection.by_attribute == {
            values["black"].attribute_id: tuple(
                sorted((values["black"].id, values["white"].id))
            ),
            values["two_m"].attribute_id: (values["two_m"].id,),
        }
        assert selection.unknown == ()
        assert sorted(selection.groups) == sorted(
            selection.by_attribute.values()
        )

    def test_an_unknown_id_is_a_group_of_its_own(self, values):
        selection = _select(values, "black", extra=[UNKNOWN_VALUE_ID])

        assert selection.unknown == (UNKNOWN_VALUE_ID,)
        assert selection.groups[-1] == (UNKNOWN_VALUE_ID,)
        assert list(selection.by_attribute) == [values["black"].attribute_id]

    def test_siblings_are_every_value_of_each_selected_attribute(self, values):
        selection = _select(values, "black", "two_m")

        assert selection.sibling_value_ids() == {
            values["black"].attribute_id: frozenset(
                {values["black"].id, values["white"].id}
            ),
            values["two_m"].attribute_id: frozenset(
                {values["one_m"].id, values["two_m"].id}
            ),
        }


@pytest.mark.django_db
class TestFilterProducts:
    def test_values_of_different_attributes_must_all_match(
        self, values, products
    ):
        assert _kept(_select(values, "black", "two_m"), products) == {"p1"}

    def test_values_of_one_attribute_are_alternatives(self, values, products):
        assert _kept(_select(values, "black", "white"), products) == set(GRID)

    def test_alternatives_and_another_attribute_combine(self, values, products):
        assert _kept(_select(values, "black", "white", "two_m"), products) == {
            "p1",
            "p4",
            "p5",
        }

    def test_an_unknown_value_matches_nothing(self, values, products):
        selection = _select(values, "black", extra=[UNKNOWN_VALUE_ID])

        assert _kept(selection, products) == set()

    def test_a_product_with_both_alternatives_is_listed_once(
        self, values, products
    ):
        ProductAttributeFactory(
            product=products["p1"], attribute_value=values["white"]
        )

        kept = _select(values, "black", "white").filter_products(
            Product.objects.filter(pk=products["p1"].pk)
        )

        assert list(kept) == [products["p1"]]


@pytest.mark.django_db
class TestFilterSearch:
    def _filters(self, selection, *, with_counts):
        search_qs = ProductTranslation.meilisearch.paginate(limit=1)
        return selection.filter_search(search_qs, with_counts=with_counts)

    def test_one_clause_per_attribute(self, values):
        selection = _select(values, "black", "white", "two_m")

        filters = self._filters(selection, with_counts=False).filters

        assert sorted(filters) == sorted(
            f"{ATTRIBUTE_VALUES_FIELD} IN {list(ids)}"
            for ids in selection.by_attribute.values()
        )
        assert len(filters) == 2

    def test_an_unknown_value_is_a_clause_that_matches_nothing(self, values):
        selection = _select(values, "black", extra=[UNKNOWN_VALUE_ID])

        filters = self._filters(selection, with_counts=False).filters

        assert f"{ATTRIBUTE_VALUES_FIELD} IN [{UNKNOWN_VALUE_ID}]" in filters
        assert len(filters) == 2

    def test_counted_groups_carry_the_siblings_of_their_attribute(self, values):
        selection = _select(values, "black", "two_m")

        state = self._filters(selection, with_counts=True)._state

        assert state.facet_groups == [
            FacetGroup(
                facet=ATTRIBUTE_VALUES_FIELD,
                clause=f"{ATTRIBUTE_VALUES_FIELD} IN [{values['black'].id}]",
                selected=frozenset({str(values["black"].id)}),
                members=frozenset(
                    {str(values["black"].id), str(values["white"].id)}
                ),
            ),
            FacetGroup(
                facet=ATTRIBUTE_VALUES_FIELD,
                clause=f"{ATTRIBUTE_VALUES_FIELD} IN [{values['two_m'].id}]",
                selected=frozenset({str(values["two_m"].id)}),
                members=frozenset(
                    {str(values["one_m"].id), str(values["two_m"].id)}
                ),
            ),
        ]

    def test_siblings_are_not_loaded_when_no_facet_is_read(
        self, values, django_assert_num_queries
    ):
        selection = _select(values, "black", "two_m")

        with django_assert_num_queries(0):
            self._filters(selection, with_counts=False)
