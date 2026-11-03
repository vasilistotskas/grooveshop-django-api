"""``?brand=`` and ``?tags=`` on the public product list."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status

from product.factories.brand import BrandFactory
from product.factories.product import ProductFactory
from tag.factories import TaggedProductFactory
from tag.factories.tag import TagFactory

pytestmark = pytest.mark.django_db


def ids(client, **params):
    response = client.get(reverse("product-list"), params)
    assert response.status_code == status.HTTP_200_OK, response.json()
    return {row["id"] for row in response.json()["results"]}


@pytest.fixture
def shop(client):
    brand_a, brand_b = BrandFactory(), BrandFactory()
    tag_x, tag_y = TagFactory(active=True), TagFactory(active=True)
    hidden = TagFactory(active=False)
    a = ProductFactory(active=True, brand=brand_a)
    b = ProductFactory(active=True, brand=brand_b)
    c = ProductFactory(active=True, brand=None)
    TaggedProductFactory(content_object=a, tag=tag_x)
    TaggedProductFactory(content_object=a, tag=tag_y)
    TaggedProductFactory(content_object=b, tag=tag_y)
    TaggedProductFactory(content_object=c, tag=hidden)
    return {
        "client": client,
        "a": a,
        "b": b,
        "c": c,
        "brand_a": brand_a,
        "brand_b": brand_b,
        "tag_x": tag_x,
        "tag_y": tag_y,
        "hidden": hidden,
    }


class TestBrand:
    def test_one_brand(self, shop):
        assert ids(shop["client"], brand=shop["brand_a"].pk) == {shop["a"].pk}

    def test_any_of_several_brands(self, shop):
        got = ids(
            shop["client"], brand=[shop["brand_a"].pk, shop["brand_b"].pk]
        )

        assert got == {shop["a"].pk, shop["b"].pk}

    def test_an_unknown_brand_is_a_400(self, shop):
        response = shop["client"].get(reverse("product-list"), {"brand": 99999})

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestTags:
    def test_one_tag(self, shop):
        assert ids(shop["client"], tags=shop["tag_x"].pk) == {shop["a"].pk}

    def test_any_of_several_tags_and_no_duplicate_rows(self, shop):
        response = shop["client"].get(
            reverse("product-list"),
            {"tags": [shop["tag_x"].pk, shop["tag_y"].pk]},
        )

        rows = [row["id"] for row in response.json()["results"]]
        assert sorted(rows) == sorted([shop["a"].pk, shop["b"].pk])

    def test_an_inactive_tag_is_a_400_like_an_unknown_one(self, shop):
        response = shop["client"].get(
            reverse("product-list"), {"tags": shop["hidden"].pk}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_tags_and_brand_narrow_together(self, shop):
        got = ids(
            shop["client"], tags=shop["tag_y"].pk, brand=shop["brand_b"].pk
        )

        assert got == {shop["b"].pk}
