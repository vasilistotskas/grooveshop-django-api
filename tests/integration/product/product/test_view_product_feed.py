import json

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.attribute import AttributeFactory
from product.factories.attribute_value import AttributeValueFactory
from product.factories.category import ProductCategoryFactory
from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.factories.product_attribute import ProductAttributeFactory
from product.factories.variant_group import ProductVariantGroupFactory
from vat.factories import VatFactory


class ProductFeedTestCase(APITestCase):
    """``/product/feed`` is the product list for catalogue feeds.

    Until the list slims down to what a product card reads, the two
    answer one query with the same rows in the same shape.
    """

    @classmethod
    def setUpTestData(cls):
        cls.category = ProductCategoryFactory()
        cls.other_category = ProductCategoryFactory()
        cls.vat = VatFactory()
        cls.group = ProductVariantGroupFactory(active=True)
        cls.attribute = AttributeFactory(active=True, is_variant=True)
        cls.products = [cls._product(index, cls.category) for index in range(4)]
        cls._product(4, cls.other_category)

    @classmethod
    def _product(cls, index, category):
        # Every value the list emits is pinned, so the comparison does
        # not lean on two calls happening to read the same random row.
        product = ProductFactory(
            slug=f"feed-product-{index}",
            category=category,
            vat=cls.vat,
            variant_group=cls.group,
            active=True,
            stock=10 + index,
            price=Money(20 + index, "EUR"),
            discount_percent=0,
            view_count=100 * (5 - index),
            num_images=0,
            num_reviews=0,
        )
        ProductImageFactory(product=product, is_main=True)
        ProductImageFactory(product=product, is_main=False)
        ProductAttributeFactory(
            product=product,
            attribute_value=AttributeValueFactory(
                attribute=cls.attribute, active=True
            ),
        )
        return product

    def get(self, route, **query):
        response = self.client.get(reverse(route), query)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return json.loads(response.content)

    def assert_same_answer(self, **query):
        listed = self.get("product-list", **query)
        fed = self.get("product-feed", **query)
        # ``links`` name the route itself; everything else is one answer.
        listed.pop("links")
        fed.pop("links")
        self.assertEqual(
            json.dumps(fed, sort_keys=True), json.dumps(listed, sort_keys=True)
        )
        return fed

    def test_answers_like_the_list_by_default(self):
        fed = self.assert_same_answer()
        self.assertEqual(fed["count"], 5)

    def test_answers_like_the_list_filtered_sorted_and_paged(self):
        fed = self.assert_same_answer(
            category=self.category.pk,
            ordering="-viewCount",
            pageSize=2,
            page=2,
            languageCode="en",
        )
        self.assertEqual(
            [row["id"] for row in fed["results"]],
            [self.products[2].pk, self.products[3].pk],
        )

    def test_carries_the_fields_catalogue_feeds_read(self):
        row = self.get("product-feed", pageSize=1)["results"][0]
        for field in ("attributes", "imagePaths", "variantGroupTitle"):
            self.assertIn(field, row)
        self.assertEqual(len(row["imagePaths"]), 2)
        self.assertEqual(len(row["attributes"]), 1)

    def test_query_count_does_not_grow_with_the_page(self):
        def queries(page_size):
            with CaptureQueriesContext(connection) as captured:
                self.get("product-feed", pageSize=page_size)
            return len(captured)

        self.assertEqual(queries(5), queries(1))
