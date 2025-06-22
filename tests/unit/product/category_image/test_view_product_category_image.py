from unittest.mock import Mock

from django.test import TestCase

from product.serializers.category_image import (
    ProductCategoryImageDetailSerializer,
    ProductCategoryImageSerializer,
    ProductCategoryImageWriteSerializer,
)
from product.views.category_image import ProductCategoryImageViewSet


class TestProductCategoryImageViewSetSerializers(TestCase):
    def setUp(self):
        self.viewset = ProductCategoryImageViewSet()
        self.viewset.request = Mock()
        self.viewset.request.query_params = Mock()
        self.viewset.request.query_params.get.return_value = None
        self.viewset.format_kwarg = None

    def test_list_serializer_selection(self):
        self.viewset.action = "list"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductCategoryImageSerializer)

    def test_retrieve_serializer_selection(self):
        self.viewset.action = "retrieve"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductCategoryImageDetailSerializer)

        self.viewset.action = "create"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductCategoryImageWriteSerializer)

    def test_update_serializer_selection(self):
        self.viewset.action = "update"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductCategoryImageWriteSerializer)

    def test_partial_update_serializer_selection(self):
        self.viewset.action = "partial_update"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductCategoryImageWriteSerializer)

    def test_request_serializer_class(self):
        self.viewset.action = "list"
        request_serializer = self.viewset.get_request_serializer_class()
        self.assertEqual(request_serializer, ProductCategoryImageSerializer)

        self.viewset.action = "create"
        request_serializer = self.viewset.get_request_serializer_class()
        self.assertEqual(
            request_serializer, ProductCategoryImageWriteSerializer
        )

    def test_response_serializer_class(self):
        self.viewset.action = "list"
        response_serializer = self.viewset.get_response_serializer_class()
        self.assertEqual(response_serializer, ProductCategoryImageSerializer)

        self.viewset.action = "create"
        response_serializer = self.viewset.get_response_serializer_class()
        self.assertEqual(
            response_serializer, ProductCategoryImageDetailSerializer
        )

        self.viewset.action = "update"
        response_serializer = self.viewset.get_response_serializer_class()
        self.assertEqual(
            response_serializer, ProductCategoryImageDetailSerializer
        )

    def test_custom_action_serializers(self):
        self.viewset.action = "by_category"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductCategoryImageSerializer)

        self.viewset.action = "by_type"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductCategoryImageSerializer)

    def test_serializer_for_schema(self):
        self.viewset.action = "list"

        schema_info = self.viewset.get_serializer_for_schema("create")
        self.assertEqual(
            schema_info["request"], ProductCategoryImageWriteSerializer
        )
        self.assertEqual(
            schema_info["response"], ProductCategoryImageDetailSerializer
        )

        schema_info = self.viewset.get_serializer_for_schema("list")
        self.assertEqual(schema_info["request"], ProductCategoryImageSerializer)
        self.assertEqual(
            schema_info["response"], ProductCategoryImageSerializer
        )

        self.assertEqual(self.viewset.action, "list")

    def test_get_serializer_context(self):
        self.viewset.action = "create"
        context = self.viewset.get_serializer_context()

        self.assertIn("action", context)
        self.assertIn("view", context)
        self.assertEqual(context["action"], "create")
        self.assertEqual(context["view"], self.viewset)

    def test_filterset_fields_configuration(self):
        expected_fields = ["id", "category", "image_type", "active"]
        self.assertEqual(
            set(self.viewset.filterset_fields), set(expected_fields)
        )

    def test_search_fields_configuration(self):
        expected_fields = ["translations__title", "translations__alt_text"]
        self.assertEqual(set(self.viewset.search_fields), set(expected_fields))

    def test_ordering_fields_configuration(self):
        expected_fields = ["created_at", "image_type", "sort_order"]
        self.assertEqual(
            set(self.viewset.ordering_fields), set(expected_fields)
        )

    def test_default_ordering(self):
        expected_ordering = ["sort_order", "-created_at"]
        self.assertEqual(self.viewset.ordering, expected_ordering)

    def test_queryset_optimization(self):
        queryset = self.viewset.get_queryset()
        self.assertIsNotNone(queryset)
