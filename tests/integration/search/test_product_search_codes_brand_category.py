"""Live product search by code, brand name and category name.

A shopper (or an agent through the gateway's ``search_products``, which
calls the same endpoint) types what is printed on the box or shown in the
menu: the SKU, the barcode, the manufacturer's part number, the brand or
the category. Each test indexes its own documents with ``LiveSearchIndex``
and searches for a token only those documents contain (see
``tests/utils/meilisearch.py``).
"""

import secrets

import pytest
from django.apps import apps
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.factories.brand import BrandFactory
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from tests.conftest import requires_meilisearch
from tests.utils.meilisearch import (
    LiveSearchIndex,
    unique_greek_word,
    unique_marker,
)


@pytest.fixture
def live_index(db):
    with LiveSearchIndex() as index:
        yield index


def make_product(name, **fields):
    return ProductFactory(
        stock=5,
        active=True,
        translations=[ProductTranslation(language_code="el", name=name)],
        **fields,
    )


def make_category(names):
    translation_model = apps.get_model("product", "ProductCategoryTranslation")
    return ProductCategoryFactory(
        translations=[
            translation_model(language_code=code, name=name)
            for code, name in names.items()
        ]
    )


def search(**params):
    response = APIClient().get(reverse("search-product"), params)
    assert response.status_code == status.HTTP_200_OK, response.data
    return response.data


def ids(data):
    return [result["id"] for result in data["results"]]


def el(product):
    return product.translations.get(language_code="el")


def digits(length):
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


@requires_meilisearch
class TestCodeSearch:
    def test_sku(self, live_index):
        sku = f"{unique_marker(4).upper()}-{digits(6)}"
        product = make_product("Plain name", sku=sku)
        live_index.add(el(product))

        assert ids(search(query=sku)) == [el(product).id]

    def test_gtin(self, live_index):
        gtin = digits(13)
        product = make_product("Plain name", gtin=gtin)
        live_index.add(el(product))

        assert ids(search(query=gtin)) == [el(product).id]

    def test_mpn(self, live_index):
        mpn = f"{unique_marker(3).upper()}{digits(5)}"
        product = make_product("Plain name", mpn=mpn)
        live_index.add(el(product))

        assert ids(search(query=mpn)) == [el(product).id]

    @pytest.mark.parametrize("field", ["sku", "gtin", "mpn"])
    def test_a_code_one_digit_off_does_not_match(self, live_index, field):
        code = digits(13)
        near = code[:-1] + str((int(code[-1]) + 1) % 10)
        product = make_product("Plain name", **{field: code})
        live_index.add(el(product))

        assert ids(search(query=code)) == [el(product).id]
        assert ids(search(query=near)) == []

    def test_code_outranks_the_same_token_in_a_description(self, live_index):
        code = digits(13)
        by_code = make_product("Plain name", gtin=code)
        by_text = ProductFactory(
            stock=5,
            active=True,
            translations=[
                ProductTranslation(
                    language_code="el",
                    name="Other name",
                    description=f"barcode {code}",
                )
            ],
        )
        live_index.add(el(by_code), el(by_text))

        assert ids(search(query=code))[0] == el(by_code).id


@requires_meilisearch
class TestBrandAndCategorySearch:
    def test_brand_name(self, live_index):
        brand_name = unique_marker()
        product = make_product(
            "Plain name", brand=BrandFactory(name=brand_name)
        )
        live_index.add(el(product))

        assert ids(search(query=brand_name)) == [el(product).id]

    def test_brand_name_in_greeklish(self, live_index):
        greek, latin = unique_greek_word()
        product = make_product("Plain name", brand=BrandFactory(name=greek))
        live_index.add(el(product))

        assert ids(search(query=latin)) == [el(product).id]

    def test_category_name(self, live_index):
        category_name = unique_marker()
        category = make_category({"el": category_name})
        product = make_product("Plain name", category=category)
        live_index.add(el(product))

        assert ids(search(query=category_name)) == [el(product).id]

    def test_category_name_in_greeklish(self, live_index):
        greek, latin = unique_greek_word()
        category = make_category({"el": greek})
        product = make_product("Plain name", category=category)
        live_index.add(el(product))

        assert ids(search(query=latin)) == [el(product).id]

    def test_category_name_follows_the_document_language(self, live_index):
        greek_name, english_name = unique_marker(), unique_marker()
        category = make_category({"el": greek_name, "en": english_name})
        product = ProductFactory(
            stock=5,
            active=True,
            category=category,
            translations=[
                ProductTranslation(language_code="el", name="Plain"),
                ProductTranslation(language_code="en", name="Plain"),
            ],
        )
        live_index.add(
            product.translations.get(language_code="el"),
            product.translations.get(language_code="en"),
        )

        assert ids(search(query=english_name)) == [
            product.translations.get(language_code="en").id
        ]
