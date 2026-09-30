from django.db import models
from django.http import QueryDict
from django.test import TestCase
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import (
    CamelCaseFilterMixin,
    CamelCaseTimeStampFilterSet,
)
from core.utils.string_case import snake_to_camel


class MockModel(models.Model):
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sort_order = models.IntegerField(default=0)
    is_published = models.BooleanField(default=False)

    class Meta:
        app_label = "test_app"
        verbose_name = "Test Model"
        verbose_name_plural = "Test Models"

    def __str__(self):
        return self.name


class TestSnakeToCamel(TestCase):
    def test_basic_conversions(self):
        test_cases = [
            ("created_at", "createdAt"),
            ("updated_at", "updatedAt"),
            ("created_after", "createdAfter"),
            ("created_before", "createdBefore"),
            ("sort_order", "sortOrder"),
            ("is_published", "isPublished"),
            ("private_metadata", "privateMetadata"),
            ("country_name", "countryName"),
            ("alpha_exact", "alphaExact"),
            ("sort_order_min", "sortOrderMin"),
            ("metadata_has_key", "metadataHasKey"),
            ("currently_published", "currentlyPublished"),
        ]

        for snake_case, expected_camel in test_cases:
            with self.subTest(snake_case=snake_case):
                result = snake_to_camel(snake_case)
                self.assertEqual(result, expected_camel)

    def test_edge_cases(self):
        test_cases = [
            ("alpha", "alpha"),
            ("a", "a"),
            ("", ""),
            ("UPPERCASE", "UPPERCASE"),
            (
                "multiple_word_field_name",
                "multipleWordFieldName",
            ),
        ]

        for snake_case, expected_camel in test_cases:
            with self.subTest(snake_case=snake_case):
                result = snake_to_camel(snake_case)
                self.assertEqual(result, expected_camel)


class MockFilterSet(CamelCaseFilterMixin, filters.FilterSet):
    created_after = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
    )
    sort_order = filters.NumberFilter(
        field_name="sort_order",
    )
    is_published = filters.BooleanFilter(
        field_name="is_published",
    )

    class Meta:
        model = MockModel
        fields = ["created_at", "sort_order", "is_published"]


class TestCamelCaseFilterMixin(TestCase):
    def test_filters_stay_keyed_by_snake_case_name(self):
        filterset = MockFilterSet()

        for name in ("created_after", "sort_order", "is_published"):
            self.assertIn(name, filterset.filters)

    def test_camel_case_params_reach_snake_case_filters(self):
        query_dict = QueryDict(mutable=True)
        query_dict["createdAfter"] = "2024-01-01"
        query_dict["sortOrder"] = "1"
        query_dict["isPublished"] = "true"

        filterset = MockFilterSet(data=query_dict)

        self.assertEqual(filterset.data["created_after"], "2024-01-01")
        self.assertEqual(filterset.data["sort_order"], "1")
        self.assertEqual(filterset.data["is_published"], "true")
        self.assertTrue(filterset.form.is_valid(), filterset.form.errors)
        self.assertEqual(filterset.form.cleaned_data["sort_order"], 1)
        self.assertIs(filterset.form.cleaned_data["is_published"], True)

    def test_query_dict_conversion(self):
        query_dict = QueryDict(mutable=True)
        query_dict["createdAfter"] = "2024-01-01"
        query_dict["sortOrder"] = "1"
        query_dict["isPublished"] = "true"

        filterset = MockFilterSet(data=query_dict)

        self.assertIsNotNone(filterset.data)

    def test_regular_dict_conversion(self):
        data = {
            "createdAfter": "2024-01-01",
            "sortOrder": 1,
            "isPublished": True,
        }

        filterset = MockFilterSet(data=data)

        self.assertIsNotNone(filterset.data)

    def test_mixed_case_parameters(self):
        query_dict = QueryDict(mutable=True)
        query_dict["createdAfter"] = "2024-01-01"
        query_dict["sort_order"] = "1"

        filterset = MockFilterSet(data=query_dict)

        self.assertIsNotNone(filterset.data)


class TestCamelCaseTimeStampFilterSet(TestCase):
    def test_timestamp_filters_accept_camel_case_params(self):
        class TestFilterSet(CamelCaseTimeStampFilterSet):
            class Meta:
                model = MockModel
                fields = ["created_at", "updated_at"]

        filterset = TestFilterSet(
            data={"createdAfter": "2024-01-01", "updatedBefore": "2024-02-01"}
        )

        for name in (
            "created_after",
            "created_before",
            "updated_after",
            "updated_before",
        ):
            self.assertIn(name, filterset.filters)
        self.assertEqual(
            filterset.filters["created_after"].field_name, "created_at"
        )
        self.assertEqual(
            filterset.filters["updated_before"].field_name, "updated_at"
        )
        self.assertTrue(filterset.form.is_valid(), filterset.form.errors)
        self.assertIsNotNone(filterset.form.cleaned_data["created_after"])
        self.assertIsNotNone(filterset.form.cleaned_data["updated_before"])
