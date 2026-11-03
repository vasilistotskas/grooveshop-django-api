"""``?brand=`` and ``?tags=`` on the public product list."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework import status

from product.models.brand import Brand
from product.models.product import Product
from tag.models.tag import Tag
from tag.models.tagged_item import TaggedItem
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db


def ids(client, **params):
    response = client.get(reverse("product-list"), params)
    assert response.status_code == status.HTTP_200_OK, response.json()
    return {row["id"] for row in response.json()["results"]}


def product(sku, **fields):
    return Product.objects.create(
        sku=sku,
        price=Decimal("10.00"),
        active=True,
        stock=5,
        vat=VatFactory(value=Decimal("20.0")),
        **fields,
    )


def tag_product(item, tag):
    TaggedItem.objects.create(
        tag=tag,
        content_type=ContentType.objects.get_for_model(Product),
        object_id=item.pk,
    )


@pytest.fixture
def shop(client):
    brand_a = Brand.objects.create(name="Brand A")
    brand_b = Brand.objects.create(name="Brand B")
    tag_x = Tag.objects.create(active=True)
    tag_y = Tag.objects.create(active=True)
    hidden = Tag.objects.create(active=False)
    a = product("BT-A", brand=brand_a)
    b = product("BT-B", brand=brand_b)
    c = product("BT-C")
    tag_product(a, tag_x)
    tag_product(a, tag_y)
    tag_product(b, tag_y)
    tag_product(c, hidden)
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
