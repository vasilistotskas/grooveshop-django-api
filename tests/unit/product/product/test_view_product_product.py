from unittest.mock import Mock

from django.test import TestCase

from core.utils.serializers import MultiSerializerMixin
from product.filters.product import ProductFilter
from product.serializers.product import (
    ProductDetailSerializer,
    ProductSerializer,
    ProductWriteSerializer,
)
from product.views.product import ProductViewSet


class ProductViewSetTestCase(TestCase):
    def setUp(self):
        self.viewset = ProductViewSet()

        self.mock_request = Mock()
        self.mock_request.query_params.get.return_value = None
        self.mock_request.user.is_authenticated = False
        self.viewset.request = self.mock_request

        mock_queryset = Mock()
        mock_queryset.with_all_annotations.return_value = mock_queryset
        self.viewset.get_queryset = Mock(return_value=mock_queryset)

    def test_list_action_serializer(self):
        self.viewset.action = "list"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductSerializer)

    def test_retrieve_action_serializer(self):
        self.viewset.action = "retrieve"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductDetailSerializer)

    def test_create_action_serializer(self):
        self.viewset.action = "create"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductWriteSerializer)

    def test_update_action_serializer(self):
        self.viewset.action = "update"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductWriteSerializer)

    def test_partial_update_action_serializer(self):
        self.viewset.action = "partial_update"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductWriteSerializer)

    def test_request_serializer_class(self):
        self.viewset.action = "create"
        request_serializer = self.viewset.get_request_serializer_class()
        self.assertEqual(request_serializer, ProductWriteSerializer)

    def test_response_serializer_class(self):
        self.viewset.action = "create"
        response_serializer = self.viewset.get_response_serializer_class()
        self.assertEqual(response_serializer, ProductDetailSerializer)

        self.viewset.action = "update"
        response_serializer = self.viewset.get_response_serializer_class()
        self.assertEqual(response_serializer, ProductDetailSerializer)

    def test_custom_action_serializers(self):
        self.viewset.action = "reviews"
        serializer_class = self.viewset.get_serializer_class()
        self.assertIsNotNone(serializer_class)

        self.viewset.action = "images"
        serializer_class = self.viewset.get_serializer_class()
        self.assertIsNotNone(serializer_class)

        self.viewset.action = "tags"
        serializer_class = self.viewset.get_serializer_class()
        self.assertIsNotNone(serializer_class)

    def test_get_serializer_for_schema(self):
        schema_info = self.viewset.get_serializer_for_schema("create")
        self.assertEqual(schema_info["request"], ProductWriteSerializer)
        self.assertEqual(schema_info["response"], ProductDetailSerializer)

        schema_info = self.viewset.get_serializer_for_schema("list")
        self.assertEqual(schema_info["request"], ProductSerializer)
        self.assertEqual(schema_info["response"], ProductSerializer)

        original_action = self.viewset.action
        self.viewset.get_serializer_for_schema("create")
        self.assertEqual(self.viewset.action, original_action)

    def test_ordering_fields_configuration(self):
        expected_fields = [
            "price",
            "created_at",
            "discount_value_amount",
            "final_price_amount",
            "price_save_percent_field",
            "review_average_field",
            "likes_count_field",
            "view_count",
            "stock",
        ]
        self.assertEqual(
            set(self.viewset.ordering_fields), set(expected_fields)
        )

    def test_search_fields_configuration(self):
        expected_fields = [
            "translations__name",
            "translations__description",
            "slug",
        ]
        self.assertEqual(set(self.viewset.search_fields), set(expected_fields))

    def test_default_ordering(self):
        self.assertEqual(self.viewset.ordering, ["-created_at"])

    def test_filterset_class_configuration(self):
        self.assertEqual(self.viewset.filterset_class, ProductFilter)

    def test_response_serializers_configuration(self):
        expected_response_serializers = {
            "create": ProductDetailSerializer,
            "update": ProductDetailSerializer,
            "partial_update": ProductDetailSerializer,
        }
        self.assertEqual(
            self.viewset.response_serializers, expected_response_serializers
        )

    def test_serializers_configuration(self):
        expected_serializers = {
            "default": ProductDetailSerializer,
            "list": ProductSerializer,
            "retrieve": ProductDetailSerializer,
            "create": ProductWriteSerializer,
            "update": ProductWriteSerializer,
            "partial_update": ProductWriteSerializer,
            "update_view_count": ProductDetailSerializer,
        }

        for action, expected_serializer in expected_serializers.items():
            self.assertIn(action, self.viewset.serializers)
            self.assertEqual(
                self.viewset.serializers[action], expected_serializer
            )

    def test_multi_serializer_mixin_integration(self):
        self.assertTrue(issubclass(ProductViewSet, MultiSerializerMixin))

    def test_queryset_optimization(self):
        queryset = self.viewset.get_queryset()
        self.assertIsNotNone(queryset)
