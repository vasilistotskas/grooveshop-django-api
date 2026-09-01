"""Categories must publish their MAIN image, without an N+1.

The storefront's category rail
(``app/components/Product/Categories/Slider.vue``) renders
``item.mainImagePath``, but no category serializer exposed it — so every
tile fell through to ``ImgWithFallback``'s placeholder no matter how
many ``ProductCategoryImage`` rows the merchant had uploaded. The field
was missing from the OpenAPI contract too, so the generated Zod schema
stripped it even if it had been sent.

``main_image`` resolves through a per-row query, so publishing it on a
LIST endpoint is only safe with the prefetch — hence the query-count
test, which is the part most likely to regress silently.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.enum.category import CategoryImageTypeEnum
from product.factories.category import ProductCategoryFactory
from product.models.category import ProductCategory
from product.models.category_image import ProductCategoryImage

IMAGE = "uploads/categories/example.jpg"


def _with_main_image(category, image=IMAGE, active=True):
    return ProductCategoryImage.objects.create(
        category=category,
        image=image,
        image_type=CategoryImageTypeEnum.MAIN,
        active=active,
    )


@pytest.mark.django_db
class TestCategoryMainImagePath:
    def test_list_publishes_main_image_path(self):
        category = ProductCategoryFactory()
        _with_main_image(category)

        response = APIClient().get(reverse("product-category-list"))

        assert response.status_code == status.HTTP_200_OK
        row = next(
            item
            for item in response.json()["results"]
            if item["id"] == category.id
        )
        assert row["mainImagePath"].endswith("example.jpg")

    def test_empty_string_when_the_category_has_no_image(self):
        category = ProductCategoryFactory()

        response = APIClient().get(reverse("product-category-list"))

        row = next(
            item
            for item in response.json()["results"]
            if item["id"] == category.id
        )
        assert row["mainImagePath"] == ""

    def test_inactive_image_is_not_published(self):
        """The prefetch filter must match
        ``CategoryImageQuerySet.get_main_image`` exactly, or the
        prefetched list disagrees with the non-prefetched fallback."""
        category = ProductCategoryFactory()
        _with_main_image(category, active=False)

        response = APIClient().get(reverse("product-category-list"))

        row = next(
            item
            for item in response.json()["results"]
            if item["id"] == category.id
        )
        assert row["mainImagePath"] == ""

    def test_non_main_image_type_is_not_published(self):
        category = ProductCategoryFactory()
        ProductCategoryImage.objects.create(
            category=category,
            image=IMAGE,
            image_type=CategoryImageTypeEnum.BANNER,
            active=True,
        )

        response = APIClient().get(reverse("product-category-list"))

        row = next(
            item
            for item in response.json()["results"]
            if item["id"] == category.id
        )
        assert row["mainImagePath"] == ""


@pytest.mark.django_db
class TestNoNPlusOne:
    def test_main_image_is_prefetched_for_a_list(self, count_queries):
        """Serializing N categories must not cost N image queries.

        Asserted on the queryset rather than the view so the number is
        about the prefetch, not about pagination or middleware.
        """
        for _ in range(10):
            _with_main_image(ProductCategoryFactory())

        with count_queries(max_queries=6):
            paths = [
                category.main_image_path
                for category in ProductCategory.objects.for_list()
            ]

        assert len(paths) == 10
        assert all(path.endswith("example.jpg") for path in paths)

    def test_without_the_prefetch_it_would_query_per_row(self):
        """Guards the guard: proves the prefetch is what makes the test
        above pass, so the assertion is not vacuously true."""
        for _ in range(5):
            _with_main_image(ProductCategoryFactory())

        prefetched = list(ProductCategory.objects.for_list())
        unprefetched = list(ProductCategory.objects.all())

        assert all(
            hasattr(category, "_prefetched_main_images")
            for category in prefetched
        )
        assert not any(
            hasattr(category, "_prefetched_main_images")
            for category in unprefetched
        )
