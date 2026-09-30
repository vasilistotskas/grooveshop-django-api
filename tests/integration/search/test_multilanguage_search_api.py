"""Live product and blog-post search: what each endpoint returns.

Every test indexes its own documents with ``LiveSearchIndex`` and
searches for a marker only those documents contain, so the assertions
are exact even though every worker shares one engine (see
``tests/utils/meilisearch.py``).
"""

import secrets
from decimal import Decimal
from urllib.parse import quote

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.post import BlogPostFactory
from blog.models.post import BlogPostTranslation
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


def make_product(names, *, price="10.00", active=True, **fields):
    """A product with one translation per ``names`` entry, VAT-free so
    its final price is its price."""
    return ProductFactory(
        price=Decimal(price),
        discount_percent=0,
        vat=None,
        active=active,
        stock=5,
        translations=[
            ProductTranslation(language_code=code, name=name)
            for code, name in names.items()
        ],
        **fields,
    )


def make_post(titles, *, is_published=True):
    """A post with one translation per ``titles`` entry."""
    return BlogPostFactory(
        is_published=is_published,
        translations=[
            BlogPostTranslation(
                language_code=code, title=title, subtitle="", body=""
            )
            for code, title in titles.items()
        ],
    )


def unique_price():
    """A price no other indexed product has, to isolate one by filter."""
    return f"{10**7 + secrets.randbelow(10**8)}.37"


def translation(obj, language_code):
    return obj.translations.get(language_code=language_code)


def search(url, **params):
    response = APIClient().get(url, params)
    assert response.status_code == status.HTTP_200_OK, response.data
    return response.data


def ids(data):
    return [result["id"] for result in data["results"]]


@requires_meilisearch
class TestProductSearch:
    url = reverse("search-product")

    def test_language_code_limits_results_to_that_locale(self, live_index):
        marker = unique_marker()
        product = make_product(
            {
                "en": f"{marker} phone",
                "el": f"{marker} τηλέφωνο",
                "de": f"{marker} Telefon",
            }
        )
        live_index.add(*product.translations.all())

        greek = search(self.url, query=marker, language_code="el")
        every_locale = search(self.url, query=marker)

        assert ids(greek) == [translation(product, "el").id]
        assert sorted(ids(every_locale)) == sorted(
            product.translations.values_list("id", flat=True)
        )

    def test_greeklish_query_matches_greek_name(self, live_index):
        # A Latin word can only reach a Greek name through a greeklish
        # shadow field.
        greek, latin = unique_greek_word()
        price = unique_price()
        product = make_product({"el": f"Φορτιστής {greek}"}, price=price)
        live_index.add(translation(product, "el"))

        data = search(
            self.url,
            query=latin,
            language_code="el",
            price_min=price,
            price_max=price,
        )

        assert ids(data) == [translation(product, "el").id]

    def test_first_letter_convention_matches_through_variants_bag(
        self, live_index
    ):
        # A 7-letter word is allowed one typo and a different FIRST
        # letter costs two, so neither the name nor its canonical "x..."
        # shadow can match "h..." - only ``name_greeklish_variants`` can.
        greek, latin = unique_greek_word(syllables=3)
        price = unique_price()
        product = make_product({"el": f"χ{greek}"}, price=price)
        live_index.add(translation(product, "el"))

        data = search(
            self.url, query=f"h{latin}", price_min=price, price_max=price
        )

        assert ids(data) == [translation(product, "el").id]

    def test_percent_encoded_query_is_decoded(self, live_index):
        greek, _latin = unique_greek_word()
        product = make_product({"el": greek})
        live_index.add(translation(product, "el"))

        data = search(self.url, query=quote(greek))

        assert ids(data) == [translation(product, "el").id]

    def test_highlights_the_matched_term(self, live_index):
        marker = unique_marker()
        product = make_product({"en": f"{marker} phone"})
        live_index.add(translation(product, "en"))

        data = search(self.url, query=marker)

        formatted = data["results"][0]["formatted"]
        assert formatted["name"] == f"<mark>{marker}</mark> phone"

    def test_inactive_and_soft_deleted_products_are_not_returned(
        self, live_index
    ):
        marker = unique_marker()
        active = make_product({"en": f"{marker} active"})
        inactive = make_product({"en": f"{marker} inactive"}, active=False)
        deleted = make_product({"en": f"{marker} deleted"}, is_deleted=True)
        live_index.add(
            *(translation(p, "en") for p in (active, inactive, deleted))
        )

        data = search(self.url, query=marker)

        assert ids(data) == [translation(active, "en").id]
        assert data["estimated_total_hits"] == 1

    def test_empty_query_returns_catalogue_narrowed_by_filters(
        self, live_index
    ):
        # No query text to carry a marker: a price no other document can
        # have isolates this product instead.
        price = unique_price()
        product = make_product({"en": "Browse only"}, price=price)
        live_index.add(translation(product, "en"))

        data = search(self.url, price_min=price, price_max=price)

        assert ids(data) == [translation(product, "en").id]


@requires_meilisearch
class TestProductSearchFiltersAndOrdering:
    url = reverse("search-product")

    @pytest.fixture
    def priced(self, live_index):
        """Three products in the same search, listed cheapest first."""
        marker = unique_marker()
        created = [
            make_product({"en": f"{marker} item {price}"}, price=price)
            for price in ("30.00", "10.00", "20.00")
        ]
        translations = [translation(p, "en") for p in created]
        live_index.add(*translations)
        by_price = sorted(translations, key=lambda t: t.master.price)
        return marker, [t.id for t in by_price]

    def test_sort_orders_by_final_price(self, priced):
        marker, cheapest_first = priced

        ascending = search(self.url, query=marker, sort="finalPrice")
        descending = search(self.url, query=marker, sort="-finalPrice")

        assert ids(ascending) == cheapest_first
        assert ids(descending) == cheapest_first[::-1]

    def test_limit_and_offset_page_through_results(self, priced):
        marker, cheapest_first = priced

        first = search(
            self.url, query=marker, sort="finalPrice", limit=2, offset=0
        )
        second = search(
            self.url, query=marker, sort="finalPrice", limit=2, offset=2
        )

        assert ids(first) == cheapest_first[:2]
        assert ids(second) == cheapest_first[2:]
        assert (first["limit"], first["offset"]) == (2, 0)
        assert (second["limit"], second["offset"]) == (2, 2)
        assert first["estimated_total_hits"] == 3

    def test_price_range_keeps_only_prices_inside_it(self, priced):
        marker, cheapest_first = priced

        data = search(self.url, query=marker, price_min=15, price_max=25)

        assert ids(data) == [cheapest_first[1]]

    def test_category_filter_and_facet_distribution(self, live_index):
        marker = unique_marker()
        shoes, bags = ProductCategoryFactory(), ProductCategoryFactory()
        in_shoes = [
            make_product({"en": f"{marker} shoe {n}"}, category=shoes)
            for n in range(2)
        ]
        in_bags = make_product({"en": f"{marker} bag"}, category=bags)
        live_index.add(*(translation(p, "en") for p in [*in_shoes, in_bags]))

        filtered = search(self.url, query=marker, categories=str(shoes.id))
        faceted = search(self.url, query=marker, facets="category")

        assert sorted(ids(filtered)) == sorted(
            translation(p, "en").id for p in in_shoes
        )
        assert faceted["facet_distribution"] == {
            "category": {str(shoes.id): 2, str(bags.id): 1}
        }


@requires_meilisearch
class TestBlogPostSearch:
    url = reverse("search-blog-post")

    def test_language_code_limits_results_to_that_locale(self, live_index):
        marker = unique_marker()
        post = make_post(
            {
                "en": f"{marker} guide",
                "el": f"{marker} οδηγός",
                "de": f"{marker} Anleitung",
            }
        )
        live_index.add(*post.translations.all())

        german = search(self.url, query=marker, language_code="de")
        every_locale = search(self.url, query=marker)

        assert ids(german) == [translation(post, "de").id]
        assert sorted(ids(every_locale)) == sorted(
            post.translations.values_list("id", flat=True)
        )

    def test_unpublished_posts_are_not_returned(self, live_index):
        marker = unique_marker()
        published = make_post({"en": f"{marker} live"})
        draft = make_post({"en": f"{marker} draft"}, is_published=False)
        live_index.add(translation(published, "en"), translation(draft, "en"))

        data = search(self.url, query=marker)

        assert ids(data) == [translation(published, "en").id]

    def test_greeklish_query_matches_greek_title(self, live_index):
        greek, latin = unique_greek_word()
        post = make_post({"el": f"Οδηγός {greek}"})
        live_index.add(translation(post, "el"))

        data = search(self.url, query=latin, language_code="el")

        assert ids(data) == [translation(post, "el").id]

    def test_limit_and_offset_page_through_results(self, live_index):
        marker = unique_marker()
        posts = [make_post({"en": f"{marker} part {n}"}) for n in range(3)]
        live_index.add(*(translation(p, "en") for p in posts))

        first = search(self.url, query=marker, limit=2, offset=0)
        second = search(self.url, query=marker, limit=2, offset=2)

        assert len(first["results"]) == 2
        assert len(second["results"]) == 1
        assert sorted(ids(first) + ids(second)) == sorted(
            translation(p, "en").id for p in posts
        )
        assert first["estimated_total_hits"] == 3

    def test_query_is_required(self, db):
        response = APIClient().get(self.url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
