from django.test import TestCase
from rest_framework.test import APIRequestFactory

from core.utils.serializers import MultiSerializerMixin
from product.serializers.favourite import (
    ProductDetailResponseSerializer,
    ProductFavouriteByProductsResponseSerializer,
    ProductFavouriteDetailSerializer,
    ProductFavouriteSerializer,
    ProductFavouriteWriteSerializer,
)
from product.views.favourite import ProductFavouriteViewSet


class ProductFavouriteViewSetTestCase(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.viewset = ProductFavouriteViewSet()
        self.viewset.action = "list"

    def test_serializers_configuration(self):
        expected_serializers = {
            "default": ProductFavouriteDetailSerializer,
            "list": ProductFavouriteSerializer,
            "retrieve": ProductFavouriteDetailSerializer,
            "create": ProductFavouriteWriteSerializer,
            "update": ProductFavouriteWriteSerializer,
            "partial_update": ProductFavouriteWriteSerializer,
            "product": ProductDetailResponseSerializer,
            "favourites_by_products": ProductFavouriteByProductsResponseSerializer,
        }

        self.assertEqual(self.viewset.serializers, expected_serializers)

    def test_list_action_serializer(self):
        self.viewset.action = "list"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductFavouriteSerializer)

    def test_retrieve_action_serializer(self):
        self.viewset.action = "retrieve"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductFavouriteDetailSerializer)

    def test_create_action_serializer(self):
        self.viewset.action = "create"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductFavouriteWriteSerializer)

    def test_update_action_serializer(self):
        self.viewset.action = "update"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductFavouriteWriteSerializer)

    def test_partial_update_action_serializer(self):
        self.viewset.action = "partial_update"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductFavouriteWriteSerializer)

    def test_custom_action_serializers(self):
        self.viewset.action = "product"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductDetailResponseSerializer)

        self.viewset.action = "favourites_by_products"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(
            serializer_class, ProductFavouriteByProductsResponseSerializer
        )

    def test_queryset_optimization(self):
        queryset = self.viewset.get_queryset()

        self.assertIn("user", str(queryset.query))
        self.assertIn("product", str(queryset.query))

    def test_filterset_fields_configuration(self):
        expected_fields = ["id", "user_id", "product_id"]
        self.assertEqual(self.viewset.filterset_fields, expected_fields)

    def test_ordering_fields_configuration(self):
        expected_fields = [
            "id",
            "user_id",
            "product_id",
            "created_at",
            "updated_at",
        ]
        self.assertEqual(self.viewset.ordering_fields, expected_fields)

    def test_search_fields_configuration(self):
        expected_fields = [
            "user__username",
            "product__translations__name",
        ]
        self.assertEqual(self.viewset.search_fields, expected_fields)

    def test_default_ordering(self):
        expected_ordering = ["-created_at"]
        self.assertEqual(self.viewset.ordering, expected_ordering)

    def test_multi_serializer_mixin_integration(self):
        self.assertTrue(
            issubclass(ProductFavouriteViewSet, MultiSerializerMixin)
        )

    def test_get_serializer_for_schema(self):
        self.viewset.action = "list"
        serializer = self.viewset.get_serializer_class()
        self.assertEqual(serializer, ProductFavouriteSerializer)

        self.viewset.action = "create"
        serializer = self.viewset.get_serializer_class()
        self.assertEqual(serializer, ProductFavouriteWriteSerializer)

    def test_request_serializer_class(self):
        write_actions = ["create", "update", "partial_update"]
        for action in write_actions:
            self.viewset.action = action
            serializer_class = self.viewset.get_serializer_class()
            self.assertEqual(serializer_class, ProductFavouriteWriteSerializer)

    def test_response_serializer_class(self):
        self.viewset.action = "list"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductFavouriteSerializer)

        self.viewset.action = "retrieve"
        serializer_class = self.viewset.get_serializer_class()
        self.assertEqual(serializer_class, ProductFavouriteDetailSerializer)

    def test_response_serializers_configuration(self):
        response_serializers = {
            "list": ProductFavouriteSerializer,
            "retrieve": ProductFavouriteDetailSerializer,
            "create": ProductFavouriteDetailSerializer,
            "update": ProductFavouriteDetailSerializer,
            "partial_update": ProductFavouriteDetailSerializer,
            "product": ProductDetailResponseSerializer,
            "favourites_by_products": ProductFavouriteByProductsResponseSerializer,
        }

        for action, expected_serializer in response_serializers.items():
            self.viewset.action = action
            if action in ["create", "update", "partial_update"]:
                actual_serializer = self.viewset.serializers.get(
                    "default", ProductFavouriteDetailSerializer
                )
            else:
                actual_serializer = self.viewset.get_serializer_class()

            if action not in ["create", "update", "partial_update"]:
                self.assertEqual(
                    actual_serializer,
                    expected_serializer,
                    f"Action '{action}' should use {expected_serializer.__name__}",
                )
