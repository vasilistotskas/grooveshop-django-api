from django.test import TestCase
from django.http import QueryDict
from django.db import models
from core.filters.camel_case_filters import (
    CamelCaseFilterMixin,
    CamelCaseTimeStampFilterSet,
    snake_to_camel,
)
from django_filters import rest_framework as filters


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
    def test_filter_name_conversion(self):
        filterset = MockFilterSet()

        self.assertIn("createdAfter", filterset.base_filters)
        self.assertIn("sortOrder", filterset.base_filters)
        self.assertIn("isPublished", filterset.base_filters)

        self.assertNotIn("created_after", filterset.base_filters)
        self.assertNotIn("sort_order", filterset.base_filters)
        self.assertNotIn("is_published", filterset.base_filters)

    def test_field_name_preservation(self):
        filterset = MockFilterSet()

        self.assertEqual(
            filterset.base_filters["createdAfter"].field_name, "created_at"
        )
        self.assertEqual(
            filterset.base_filters["sortOrder"].field_name, "sort_order"
        )
        self.assertEqual(
            filterset.base_filters["isPublished"].field_name, "is_published"
        )

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

    def test_camel_case_fields_mapping(self):
        filterset = MockFilterSet()

        expected_mapping = {
            "createdAfter": "created_after",
            "createdAt": "created_at",
            "sortOrder": "sort_order",
            "isPublished": "is_published",
        }

        self.assertEqual(filterset._camel_case_fields, expected_mapping)


class TestCamelCaseTimeStampFilterSet(TestCase):
    def test_timestamp_filters_camelized(self):
        class TestFilterSet(CamelCaseTimeStampFilterSet):
            class Meta:
                model = MockModel
                fields = ["created_at", "updated_at"]

        filterset = TestFilterSet()

        self.assertIn("createdAfter", filterset.base_filters)
        self.assertIn("createdBefore", filterset.base_filters)
        self.assertIn("updatedAfter", filterset.base_filters)
        self.assertIn("updatedBefore", filterset.base_filters)

        self.assertEqual(
            filterset.base_filters["createdAfter"].field_name, "created_at"
        )
        self.assertEqual(
            filterset.base_filters["updatedBefore"].field_name, "updated_at"
        )
