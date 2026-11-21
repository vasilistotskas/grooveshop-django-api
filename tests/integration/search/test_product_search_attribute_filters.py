"""Product search by attribute value, against the live engine (#138).

Alternatives within one attribute (OR), every attribute narrowing (AND),
for the hits and for the ``attribute_values`` facet counts. The catalogue
is ``tests/utils/attributes.py``'s: six products over colour, length and
material, so a selection read as one flat OR list answers differently
from the right rule. Every test indexes its own documents and searches
for a marker only they contain (see ``tests/utils/meilisearch.py``).
"""

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from tests.conftest import requires_meilisearch
from tests.utils.attributes import GRID, assign_values, create_values
from tests.utils.meilisearch import LiveSearchIndex, unique_marker

UNKNOWN_VALUE_ID = 2**40


@pytest.fixture
def catalogue(db):
    """(marker, attribute values, product id -> grid key), all indexed."""
    marker = unique_marker()
    with LiveSearchIndex() as index:
        values = create_values()
        translations, keys = [], {}
        for key in GRID:
            product = ProductFactory(
                active=True,
                num_reviews=0,
                translations=[
                    ProductTranslation(language_code="en", name=marker)
                ],
            )
            assign_values(product, key, values)
            translations.append(product.translations.get())
            keys[product.id] = key
        index.add(*translations)
        yield marker, values, keys


def _search(marker, attribute_values, **params):
    response = APIClient().get(
        reverse("search-product"),
        {
            "query": marker,
            "language_code": "en",
            "attribute_values": attribute_values,
            **params,
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    return response.data


def _csv(values, *selected):
    return ",".join(str(values[key].id) for key in selected)


def _hits(data, keys):
    return {keys[row["master"]] for row in data["results"]}


def _counts(data, values):
    """The attribute_values facet as {value key: count}."""
    names = {str(value.id): key for key, value in values.items()}
    return {
        names[value_id]: count
        for value_id, count in data["facet_distribution"][
            "attribute_values"
        ].items()
    }


@requires_meilisearch
@pytest.mark.django_db
class TestAttributeValueFilter:
    def test_values_of_different_attributes_must_all_match(self, catalogue):
        marker, values, keys = catalogue

        data = _search(marker, _csv(values, "black", "two_m"))

        assert _hits(data, keys) == {"p1"}

    def test_values_of_one_attribute_are_alternatives(self, catalogue):
        marker, values, keys = catalogue

        data = _search(marker, _csv(values, "black", "white"))

        assert _hits(data, keys) == set(GRID)

    def test_alternatives_and_another_attribute_combine(self, catalogue):
        marker, values, keys = catalogue

        data = _search(marker, _csv(values, "black", "white", "two_m"))

        assert _hits(data, keys) == {"p1", "p4", "p5"}

    def test_a_single_value_filters_to_the_products_carrying_it(
        self, catalogue
    ):
        marker, values, keys = catalogue

        data = _search(marker, _csv(values, "wool"))

        assert _hits(data, keys) == {"p2", "p4", "p6"}

    def test_a_value_that_does_not_exist_lists_nothing(self, catalogue):
        marker, values, _keys = catalogue

        data = _search(marker, f"{_csv(values, 'black')},{UNKNOWN_VALUE_ID}")

        assert data["results"] == []


@requires_meilisearch
@pytest.mark.django_db
class TestAttributeValueFacetCounts:
    """A facet's own selection must not shrink its own counts; the other
    attributes' selections still do."""

    def test_each_attribute_is_counted_without_its_own_selection(
        self, catalogue
    ):
        marker, values, keys = catalogue

        data = _search(
            marker,
            _csv(values, "black", "two_m"),
            facets="attribute_values",
        )

        assert _hits(data, keys) == {"p1"}
        assert _counts(data, values) == {
            # colour, under length = 2 m: p1 | p4 p5
            "black": 1,
            "white": 2,
            # length, under colour = black: p1 | p2 p3
            "two_m": 1,
            "one_m": 2,
            # material is not selected: counted under both selections
            "cotton": 1,
        }

    def test_alternatives_of_one_attribute_do_not_narrow_each_other(
        self, catalogue
    ):
        marker, values, keys = catalogue

        data = _search(
            marker,
            _csv(values, "black", "white", "two_m"),
            facets="attribute_values",
        )

        assert _hits(data, keys) == {"p1", "p4", "p5"}
        assert _counts(data, values) == {
            # colour, under length = 2 m: p1 | p4 p5
            "black": 1,
            "white": 2,
            # length, under colour = black OR white: every product
            "two_m": 3,
            "one_m": 3,
            # material, under both selections: p1 p5 | p4
            "cotton": 2,
            "wool": 1,
        }

    def test_a_single_attribute_still_shows_its_alternatives(self, catalogue):
        marker, values, keys = catalogue

        data = _search(marker, _csv(values, "black"), facets="attribute_values")

        assert _hits(data, keys) == {"p1", "p2", "p3"}
        assert _counts(data, values) == {
            "black": 3,
            "white": 3,
            "two_m": 1,
            "one_m": 2,
            "cotton": 2,
            "wool": 1,
        }

    def test_other_facets_follow_every_selection(self, catalogue):
        marker, values, _keys = catalogue

        data = _search(
            marker,
            _csv(values, "black", "two_m"),
            facets="attribute_values,language_code",
        )

        assert data["facet_distribution"]["language_code"] == {"en": 1}

    def test_without_the_facet_the_search_is_unchanged(self, catalogue):
        marker, values, keys = catalogue

        data = _search(marker, _csv(values, "black", "two_m"))

        assert _hits(data, keys) == {"p1"}
        assert "facet_distribution" not in data
