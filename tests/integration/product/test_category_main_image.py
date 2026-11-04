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
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation
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


def _alt_row(category, language="el"):
    response = APIClient().get(
        reverse("product-category-list"), HTTP_ACCEPT_LANGUAGE=language
    )
    assert response.status_code == status.HTTP_200_OK
    return next(
        item for item in response.json()["results"] if item["id"] == category.id
    )


def _write_alt(image, **alt_by_language):
    for language, alt in alt_by_language.items():
        image.set_current_language(language)
        image.alt_text = alt
        image.save()


@pytest.mark.django_db
class TestCategoryMainImageAlt:
    def test_the_alt_text_is_served_in_the_request_language(self):
        category = ProductCategoryFactory()
        _write_alt(
            _with_main_image(category), el="Μαύρο καλώδιο", en="Black cable"
        )

        assert _alt_row(category, "el")["mainImageAlt"] == "Μαύρο καλώδιο"
        assert _alt_row(category, "en")["mainImageAlt"] == "Black cable"

    def test_another_language_fills_in_when_the_current_has_none(self):
        category = ProductCategoryFactory()
        _write_alt(_with_main_image(category), el="Μαύρο καλώδιο")

        assert _alt_row(category, "en")["mainImageAlt"] == "Μαύρο καλώδιο"

    def test_any_language_fills_in_when_the_request_language_has_none(self):
        category = ProductCategoryFactory()
        _write_alt(_with_main_image(category), en="Black cable")

        assert _alt_row(category, "el")["mainImageAlt"] == "Black cable"

    def test_empty_without_a_main_image(self):
        category = ProductCategoryFactory()

        assert _alt_row(category)["mainImageAlt"] == ""

    def test_empty_when_the_merchant_wrote_no_alt_text(self):
        category = ProductCategoryFactory()
        _with_main_image(category)

        row = _alt_row(category)

        assert row["mainImageAlt"] == ""
        assert row["mainImageAlt"] != category.safe_translation_getter(
            "name", any_language=True
        )

    def test_a_banner_alt_text_is_not_the_main_images(self):
        category = ProductCategoryFactory()
        banner = ProductCategoryImage.objects.create(
            category=category,
            image=IMAGE,
            image_type=CategoryImageTypeEnum.BANNER,
            active=True,
        )
        _write_alt(banner, el="Banner")

        assert _alt_row(category)["mainImageAlt"] == ""

    def test_the_detail_serves_it_too(self):
        category = ProductCategoryFactory()
        _write_alt(_with_main_image(category), el="Μαύρο καλώδιο")

        response = APIClient().get(
            reverse("product-category-detail", args=[category.pk])
        )

        assert response.json()["mainImageAlt"] == "Μαύρο καλώδιο"


@pytest.mark.django_db
class TestNoNPlusOne:
    def test_the_alt_text_costs_the_same_for_one_category_or_many(self):
        def alts():
            with translation.override("el"):
                return [
                    category.main_image_alt
                    for category in ProductCategory.objects.for_list()
                ]

        _write_alt(_with_main_image(ProductCategoryFactory()), el="Ένα")
        with CaptureQueriesContext(connection) as one:
            assert alts() == ["Ένα"]

        for n in range(5):
            _write_alt(
                _with_main_image(ProductCategoryFactory()), el=f"Alt {n}"
            )
        with CaptureQueriesContext(connection) as many:
            assert len(alts()) == 6

        assert len(many) == len(one)

    def test_main_image_is_prefetched_for_a_list(
        self, django_assert_max_num_queries
    ):
        """Serializing N categories must not cost N image queries.

        Asserted on the queryset rather than the view so the number is
        about the prefetch, not about pagination or middleware.
        """
        for _ in range(10):
            _with_main_image(ProductCategoryFactory())

        with django_assert_max_num_queries(6):
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
