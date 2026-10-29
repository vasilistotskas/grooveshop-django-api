from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
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


def _product(category, vat, **kwargs):
    return ProductFactory(
        category=category, vat=vat, active=True, stock=5, **kwargs
    )


def _group(**names):
    group = ProductVariantGroupFactory(active=True)
    # The factory seeds random names in every language; keep only ours.
    group.translations.all().delete()
    for language, name in names.items():
        group.set_current_language(language)
        group.name = name
    group.save()
    return group


class ProductFeedFieldsTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = ProductCategoryFactory()
        cls.vat = VatFactory()
        cls.group = _group(el="Ομάδα", en="Group")
        cls.product = _product(cls.category, cls.vat, variant_group=cls.group)

        # Created out of display order on purpose: the main image comes
        # last, and ``sort_order`` (assigned on save) is rewritten so the
        # insertion order is not the display order either.
        cls.second = ProductImageFactory(product=cls.product, is_main=False)
        cls.first = ProductImageFactory(product=cls.product, is_main=False)
        cls.main = ProductImageFactory(product=cls.product, is_main=True)
        for image, sort_order in (
            (cls.second, 2),
            (cls.first, 1),
            (cls.main, 9),
        ):
            type(image).objects.filter(pk=image.pk).update(
                sort_order=sort_order
            )

        cls.colour = AttributeFactory(active=True, is_variant=True)
        cls.material = AttributeFactory(active=True, is_variant=False)
        cls.colour_value = AttributeValueFactory(
            attribute=cls.colour, active=True
        )
        cls.material_value = AttributeValueFactory(
            attribute=cls.material, active=True
        )
        ProductAttributeFactory(
            product=cls.product, attribute_value=cls.colour_value
        )
        ProductAttributeFactory(
            product=cls.product, attribute_value=cls.material_value
        )

    def feed_row(self, product=None, **headers):
        response = self.client.get(
            reverse("product-feed"), **headers, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pk = (product or self.product).pk
        return next(r for r in response.data["results"] if r["id"] == pk)

    def detail(self, product=None, **headers):
        response = self.client.get(
            reverse("product-detail", args=[(product or self.product).pk]),
            **headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def test_attributes_expose_is_variant_per_attribute(self):
        for payload in (self.feed_row(), self.detail()):
            flags = {
                a["attribute_id"]: a["is_variant"]
                for a in payload["attributes"]
            }
            self.assertEqual(
                flags, {self.colour.id: True, self.material.id: False}
            )

    def test_image_paths_are_main_first_then_sort_order(self):
        expected = [
            self.main.main_image_path,
            self.first.main_image_path,
            self.second.main_image_path,
        ]
        self.assertEqual(self.feed_row()["image_paths"], expected)
        self.assertEqual(self.detail()["image_paths"], expected)

    def test_image_paths_share_the_main_image_path_format(self):
        row = self.feed_row()
        self.assertEqual(row["image_paths"][0], row["main_image_path"])
        self.assertTrue(row["image_paths"][0].startswith("media/"))

    def test_image_paths_empty_without_images(self):
        bare = _product(self.category, self.vat)
        self.assertEqual(self.feed_row(bare)["image_paths"], [])
        self.assertEqual(self.detail(bare)["image_paths"], [])

    def test_variant_group_title_follows_the_request_language(self):
        for language, title in (("el", "Ομάδα"), ("en", "Group")):
            headers = {"headers": {"Accept-Language": language}}
            self.assertEqual(
                self.feed_row(**headers)["variant_group_title"], title
            )
            self.assertEqual(
                self.detail(**headers)["variant_group_title"], title
            )

    def test_variant_group_title_falls_back_to_another_language(self):
        only_greek = _product(
            self.category, self.vat, variant_group=_group(el="Μόνο ελληνικά")
        )
        row = self.feed_row(only_greek, headers={"Accept-Language": "en"})
        self.assertEqual(row["variant_group_title"], "Μόνο ελληνικά")

    def test_variant_group_title_is_null_without_a_group(self):
        ungrouped = _product(self.category, self.vat)
        self.assertIsNone(self.feed_row(ungrouped)["variant_group_title"])
        self.assertIsNone(self.detail(ungrouped)["variant_group_title"])

    def test_variant_group_title_is_null_when_the_group_has_no_name(self):
        nameless = _product(
            self.category, self.vat, variant_group=_group(el="")
        )
        self.assertIsNone(self.feed_row(nameless)["variant_group_title"])

    def test_feed_query_count_does_not_grow_with_rows(self):
        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse("product-feed"))
            return len(ctx)

        queries()  # warm per-process caches
        before = queries()
        for _ in range(3):
            product = _product(
                self.category, self.vat, variant_group=_group(el="Άλλη")
            )
            ProductImageFactory(product=product, is_main=True)
            ProductImageFactory(product=product, is_main=False)
            ProductAttributeFactory(
                product=product, attribute_value=self.colour_value
            )
        self.assertEqual(queries(), before)
