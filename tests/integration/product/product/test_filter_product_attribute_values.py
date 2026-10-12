"""``attribute_value__in`` on the product list: alternatives within one
attribute, every attribute narrowing (the same rule as the search, which
both read through ``AttributeSelection``)."""

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.factories.product import ProductFactory
from tests.utils.attributes import GRID, assign_values, create_values


@pytest.fixture
def catalogue(db):
    values = create_values()
    products = {}
    for key in GRID:
        product = ProductFactory(active=True, num_reviews=0)
        assign_values(product, key, values)
        products[key] = product
    return values, {p.id: key for key, p in products.items()}


def _list(**params):
    response = APIClient().get(reverse("product-list"), params)
    assert response.status_code == status.HTTP_200_OK, response.data
    return response.data


def _keys(data, by_id):
    return {by_id[row["id"]] for row in data["results"] if row["id"] in by_id}


@pytest.mark.django_db
class TestAttributeValueInFilter:
    def test_values_of_different_attributes_must_all_match(self, catalogue):
        values, by_id = catalogue
        selected = f"{values['black'].id},{values['two_m'].id}"

        assert _keys(_list(attribute_value__in=selected), by_id) == {"p1"}

    def test_values_of_one_attribute_are_alternatives(self, catalogue):
        values, by_id = catalogue
        selected = f"{values['black'].id},{values['white'].id}"

        assert _keys(_list(attribute_value__in=selected), by_id) == set(GRID)

    def test_alternatives_and_another_attribute_combine(self, catalogue):
        values, by_id = catalogue
        selected = ",".join(
            str(values[key].id) for key in ("black", "white", "two_m")
        )

        assert _keys(_list(attribute_value__in=selected), by_id) == {
            "p1",
            "p4",
            "p5",
        }

    def test_a_value_that_does_not_exist_lists_nothing(self, catalogue):
        values, by_id = catalogue

        assert not _keys(
            _list(attribute_value__in=f"{values['black'].id},{2**40}"), by_id
        )

    def test_garbage_is_a_400(self, catalogue):
        response = APIClient().get(
            reverse("product-list"), {"attribute_value__in": "1,x"}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
