"""``sidebarFacets``: one search answers the whole filter sidebar.

The storefront used to ask for each sidebar facet in a request of its own
(category counts, brand counts, attribute value counts, price bounds,
popularity ranges). Every test here makes those requests, makes the one
request with ``sidebarFacets=true``, and requires the facet the old request
was made for to be equal, over every combination of filters. Against the
live engine, on the catalogue below:

    product  category  brand  stock  discount  price  views  colour  length
    p1       c1        acme   5      10        10     1      black   2 m
    p2       c1        other  0      0         20     2      black   1 m
    p3       c2        acme   5      0         30     3      black   1 m
    p4       c2        other  5      10        40     4      white   2 m
    p5       c3        none   5      0         50     5      white   2 m
    p6       c3        acme   0      0         60     6      white   1 m

``c1`` and ``c2`` are children of ``root``; ``c3`` is in a tree of its own.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.factories.brand import BrandFactory
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from tests.conftest import requires_meilisearch
from tests.utils.attributes import assign_values, create_values
from tests.utils.meilisearch import (
    LiveSearchIndex,
    build_document,
    private_index,
    unique_marker,
)
from vat.models import Vat

SIDEBAR = "category,brand,attribute_values,effective_price,view_count"

# product -> (category, brand, stock, discount percent, net price, views)
ROWS = {
    "p1": ("c1", "acme", 5, 10, 10, 1),
    "p2": ("c1", "other", 0, 0, 20, 2),
    "p3": ("c2", "acme", 5, 0, 30, 3),
    "p4": ("c2", "other", 5, 10, 40, 4),
    "p5": ("c3", None, 5, 0, 50, 5),
    "p6": ("c3", "acme", 0, 0, 60, 6),
}


class Shop:
    """The indexed catalogue and the requests the sidebar makes."""

    def __init__(self, marker, categories, brands, values, translations):
        self.marker = marker
        self.categories = categories
        self.brands = brands
        self.values = values
        self.translations = translations
        self.ids = {key: row.master_id for key, row in translations.items()}

    def indexed_prices(self, *keys):
        """What the engine holds as ``effective_price`` for these products."""
        return [
            build_document(self.translations[key])["effective_price"]
            for key in keys
        ]

    def listing_categories(self, combo, scope):
        """The listing's categories: the page's own plus the URL's."""
        return ",".join(
            [str(self.categories[scope].id)]
            + [
                str(self.categories[key].id)
                for key in combo.get("categories", ())
            ]
        )

    def get(self, **params):
        response = APIClient().get(
            reverse("search-product"),
            {"query": self.marker, "language_code": "en", **params},
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        return response.data

    def csv(self, mapping, *keys):
        return ",".join(str(mapping[key].id) for key in keys)

    def filters(self, combo):
        """The query parameters of one filter combination."""
        params = {}
        if combo.get("categories"):
            params["categories"] = self.csv(
                self.categories, *combo["categories"]
            )
        if combo.get("brands"):
            params["brands"] = self.csv(self.brands, *combo["brands"])
        if combo.get("attributes"):
            params["attribute_values"] = self.csv(
                self.values, *combo["attributes"]
            )
        for flag in ("in_stock", "on_offer"):
            if combo.get(flag):
                params[flag] = "true"
        for bound in ("price_min", "price_max"):
            if bound in combo:
                params[bound] = combo[bound]
        return params

    def old_calls(self, combo, scope):
        """The five requests the storefront made before, as they made them."""
        params = self.filters(combo)
        listing = {
            **params,
            "categories": self.listing_categories(combo, scope),
        }

        def without(source, name):
            return {k: v for k, v in source.items() if k != name}

        return {
            # every filter but the categories, the page's own included
            "category": self.get(
                **without(params, "categories"), facets="category", limit=1
            ),
            "brand": self.get(
                **without(listing, "brands"), facets="brand", limit=1
            ),
            "attributes": self.get(
                **listing, facets="attribute_values", limit=1
            ),
            "price": self.get(
                query="",
                categories=str(self.categories[scope].id),
                facets="effective_price",
                limit=1,
            ),
            "views": self.get(query="", facets="view_count", limit=1),
        }

    def new_call(self, combo, scope):
        return self.get(
            **{
                **self.filters(combo),
                "categories": self.listing_categories(combo, scope),
            },
            facets=SIDEBAR,
            sidebar_facets="true",
            scope_category=self.categories[scope].id,
        )


@pytest.fixture
def shop(db):
    # Its own index: the price and popularity ranges are store-wide, so
    # other workers' documents must not be in them (``private_index``).
    marker = unique_marker()
    vat = Vat.objects.get_or_create(value=Decimal("24.0"))[0]
    with private_index(ProductTranslation), LiveSearchIndex() as index:
        root = ProductCategoryFactory(parent=None)
        categories = {
            "root": root,
            "c1": ProductCategoryFactory(parent=root),
            "c2": ProductCategoryFactory(parent=root),
            "c3": ProductCategoryFactory(parent=None),
        }
        brands = {"acme": BrandFactory(), "other": BrandFactory()}
        values = create_values()
        translations = {}
        for key, (cat, brand, stock, discount, price, views) in ROWS.items():
            product = ProductFactory(
                active=True,
                num_reviews=0,
                category=categories[cat],
                brand=brands[brand] if brand else None,
                stock=stock,
                discount_percent=Decimal(discount),
                price=Decimal(price),
                vat=vat,
                view_count=views,
                translations=[
                    ProductTranslation(language_code="en", name=marker)
                ],
            )
            assign_values(product, key, values)
            translations[key] = product.translations.get()
        index.add(*translations.values())
        yield Shop(marker, categories, brands, values, translations)


COMBINATIONS = [
    {},
    {"categories": ["c1"]},
    {"categories": ["root"]},
    {"categories": ["c1", "c3"]},
    {"brands": ["acme"]},
    {"brands": ["acme", "other"]},
    {"attributes": ["black"]},
    {"attributes": ["black", "white", "two_m"]},
    {"in_stock": True},
    {"on_offer": True},
    {"price_min": 15, "price_max": 55},
    {"categories": ["c2"], "brands": ["other"]},
    {"categories": ["c1"], "brands": ["acme"], "attributes": ["black"]},
    {
        "categories": ["root"],
        "brands": ["acme"],
        "attributes": ["white", "two_m"],
        "in_stock": True,
        "on_offer": True,
        "price_min": 5,
        "price_max": 100,
    },
]


@requires_meilisearch
@pytest.mark.django_db
class TestSidebarFacetsEqualTheSeparateRequests:
    @pytest.mark.parametrize("scope", ["root", "c3"])
    @pytest.mark.parametrize("combo", COMBINATIONS)
    def test_every_facet_equals_its_own_request(self, shop, combo, scope):
        old = shop.old_calls(combo, scope)
        new = shop.new_call(combo, scope)

        assert (
            new["facet_distribution"]["category"]
            == old["category"]["facet_distribution"]["category"]
        )
        assert (
            new["facet_distribution"]["brand"]
            == old["brand"]["facet_distribution"]["brand"]
        )
        assert (
            new["facet_distribution"]["attribute_values"]
            == old["attributes"]["facet_distribution"]["attribute_values"]
        )
        assert (
            new["facet_stats"]["effective_price"]
            == old["price"]["facet_stats"]["effective_price"]
        )
        assert (
            new["facet_stats"]["view_count"]
            == old["views"]["facet_stats"]["view_count"]
        )

    @pytest.mark.parametrize("combo", COMBINATIONS)
    def test_the_listing_is_what_it_was(self, shop, combo):
        params = {
            **shop.filters(combo),
            "categories": shop.listing_categories(combo, "root"),
        }

        plain = shop.get(**params)
        sidebar = shop.get(
            **params,
            facets=SIDEBAR,
            sidebar_facets="true",
            scope_category=shop.categories["root"].id,
        )

        assert [row["master"] for row in sidebar["results"]] == [
            row["master"] for row in plain["results"]
        ]
        assert sidebar["estimated_total_hits"] == plain["estimated_total_hits"]


@requires_meilisearch
@pytest.mark.django_db
class TestSidebarFacetValues:
    """The counts above are equal to the old requests'; these pin the
    values, so equality is not between two empty answers."""

    def test_category_and_brand_counts_leave_out_their_own_selection(
        self, shop
    ):
        data = shop.get(
            categories=shop.csv(shop.categories, "c1"),
            brands=shop.csv(shop.brands, "acme"),
            facets="category,brand",
            sidebar_facets="true",
        )

        assert [row["master"] for row in data["results"]] == [shop.ids["p1"]]
        # categories under brand = acme: p1 | p3 | p6
        assert data["facet_distribution"]["category"] == {
            str(shop.categories["c1"].id): 1,
            str(shop.categories["c2"].id): 1,
            str(shop.categories["c3"].id): 1,
        }
        # brands under category = c1: p1 | p2
        assert data["facet_distribution"]["brand"] == {
            str(shop.brands["acme"].id): 1,
            str(shop.brands["other"].id): 1,
        }

    def test_without_the_flag_the_counts_follow_every_filter(self, shop):
        data = shop.get(
            categories=shop.csv(shop.categories, "c1"),
            brands=shop.csv(shop.brands, "acme"),
            facets="category,brand",
        )

        assert data["facet_distribution"]["category"] == {
            str(shop.categories["c1"].id): 1
        }
        assert data["facet_distribution"]["brand"] == {
            str(shop.brands["acme"].id): 1
        }

    def test_ranges_ignore_the_query_and_the_filters(self, shop):
        data = shop.get(
            query=unique_marker(),
            brands=shop.csv(shop.brands, "acme"),
            price_min=25,
            facets="effective_price",
            sidebar_facets="true",
            scope_category=shop.categories["root"].id,
        )

        assert data["results"] == []
        # root's subtree is c1 and c2, the products p1 to p4: neither the
        # query, the brand nor the price_min narrowed the range
        prices = shop.indexed_prices("p1", "p2", "p3", "p4")
        assert data["facet_stats"]["effective_price"] == {
            "min": min(prices),
            "max": max(prices),
        }

    def test_the_scope_category_is_not_a_filter(self, shop):
        data = shop.get(
            categories=shop.csv(shop.categories, "c3"),
            facets="effective_price",
            sidebar_facets="true",
            scope_category=shop.categories["c1"].id,
        )

        assert {row["master"] for row in data["results"]} == {
            shop.ids["p5"],
            shop.ids["p6"],
        }
