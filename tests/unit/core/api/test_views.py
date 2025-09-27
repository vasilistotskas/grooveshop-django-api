from unittest.mock import Mock, patch
from django.test import TestCase, RequestFactory
from rest_framework import status
from rest_framework.request import Request

from core.api.views import (
    BaseModelViewSet,
    PaginationModelViewSet,
    Metadata,
)
from core.pagination.page_number import PageNumberPaginator
from core.pagination.cursor import CursorPaginator
from core.pagination.limit_offset import LimitOffsetPaginator
from product.serializers.product import ProductSerializer


class PaginationModelViewSetTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.viewset = PaginationModelViewSet()

    def test_default_paginator_pageNumber(self):
        request = self.factory.get("/api/test/")
        django_request = Request(request)
        self.viewset.request = django_request

        paginator = self.viewset.paginator
        self.assertIsInstance(paginator, PageNumberPaginator)

    def test_cursor_paginator(self):
        request = self.factory.get("/api/test/?pagination_type=cursor")
        django_request = Request(request)
        self.viewset.request = django_request

        paginator = self.viewset.paginator
        self.assertIsInstance(paginator, CursorPaginator)

    def test_limit_offset_paginator(self):
        request = self.factory.get("/api/test/?pagination_type=limitOffset")
        django_request = Request(request)
        self.viewset.request = django_request

        paginator = self.viewset.paginator
        self.assertIsInstance(paginator, LimitOffsetPaginator)

    def test_invalid_pagination_type_falls_back_to_default(self):
        request = self.factory.get("/api/test/?pagination_type=invalid")
        django_request = Request(request)
        self.viewset.request = django_request

        paginator = self.viewset.paginator
        self.assertIsInstance(paginator, PageNumberPaginator)

    def test_pagination_type_case_insensitive(self):
        test_cases = [
            ("CURSOR", CursorPaginator),
            ("cursor", CursorPaginator),
            ("Cursor", CursorPaginator),
            ("PAGENUMBER", PageNumberPaginator),
            ("pageNumber", PageNumberPaginator),
            ("LIMITOFFSET", LimitOffsetPaginator),
            ("limitOffset", LimitOffsetPaginator),
        ]

        for pagination_type, expected_class in test_cases:
            with self.subTest(pagination_type=pagination_type):
                request = self.factory.get(
                    f"/api/test/?pagination_type={pagination_type}"
                )
                django_request = Request(request)
                self.viewset.request = django_request

                if hasattr(self.viewset, "_paginator"):
                    delattr(self.viewset, "_paginator")

                paginator = self.viewset.paginator
                self.assertIsInstance(paginator, expected_class)

    @patch.object(PaginationModelViewSet, "get_serializer_class")
    @patch.object(PaginationModelViewSet, "get_serializer_context")
    @patch.object(PaginationModelViewSet, "paginate_queryset")
    @patch.object(PaginationModelViewSet, "get_paginated_response")
    def test_paginate_and_serialize_with_pagination(
        self,
        mock_get_paginated_response,
        mock_paginate_queryset,
        mock_get_context,
        mock_get_serializer_class,
    ):
        mock_serializer_class = Mock()
        mock_serializer_instance = Mock()
        mock_serializer_instance.data = [{"id": 1}, {"id": 2}]
        mock_serializer_class.return_value = mock_serializer_instance
        mock_get_serializer_class.return_value = mock_serializer_class
        mock_get_context.return_value = {}
        mock_queryset = Mock()
        mock_page = [{"id": 1}, {"id": 2}]
        mock_paginate_queryset.return_value = mock_page
        mock_get_paginated_response.return_value = Mock(
            data={"results": mock_page}
        )

        request = self.factory.get("/api/test/")
        django_request = Request(request)

        self.viewset.paginate_and_serialize(mock_queryset, django_request)

        mock_paginate_queryset.assert_called_once_with(mock_queryset)
        mock_get_paginated_response.assert_called_once()

    @patch.object(PaginationModelViewSet, "get_serializer_class")
    @patch.object(PaginationModelViewSet, "get_serializer_context")
    def test_paginate_and_serialize_pagination_disabled(
        self, mock_get_context, mock_get_serializer_class
    ):
        mock_serializer_class = Mock()
        mock_serializer_instance = Mock()
        mock_serializer_instance.data = [{"id": 1}, {"id": 2}]
        mock_serializer_class.return_value = mock_serializer_instance
        mock_get_serializer_class.return_value = mock_serializer_class
        mock_get_context.return_value = {}

        request = self.factory.get("/api/test/?pagination=false")
        django_request = Request(request)
        mock_queryset = Mock()

        result = self.viewset.paginate_and_serialize(
            mock_queryset, django_request
        )

        self.assertEqual(result.status_code, status.HTTP_200_OK)
        self.assertEqual(result.data, [{"id": 1}, {"id": 2}])


class BaseModelViewSetTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.viewset = BaseModelViewSet()
        self.viewset.get_serializer_class = Mock(return_value=ProductSerializer)

    def test_metadata_class_configuration(self):
        self.assertEqual(self.viewset.metadata_class, Metadata)

    def test_get_response_serializer_class_default(self):
        result = self.viewset.get_response_serializer()
        self.assertEqual(result, ProductSerializer)

    def test_get_request_serializer_class_default(self):
        result = self.viewset.get_request_serializer()
        self.assertEqual(result, ProductSerializer)

    @patch.object(BaseModelViewSet, "filter_queryset")
    @patch.object(BaseModelViewSet, "get_queryset")
    @patch.object(BaseModelViewSet, "paginate_and_serialize")
    def test_list_method(
        self, mock_paginate, mock_get_queryset, mock_filter_queryset
    ):
        mock_queryset = Mock()
        mock_filtered_queryset = Mock()
        mock_get_queryset.return_value = mock_queryset
        mock_filter_queryset.return_value = mock_filtered_queryset
        mock_paginate.return_value = Mock()

        request = self.factory.get("/api/test/")
        django_request = Request(request)

        self.viewset.list(django_request)

        mock_get_queryset.assert_called_once()
        mock_filter_queryset.assert_called_once_with(mock_queryset)
        mock_paginate.assert_called_once_with(
            mock_filtered_queryset,
            django_request,
            serializer_class=ProductSerializer,
        )

    @patch.object(BaseModelViewSet, "metadata_class")
    def test_api_schema_action(self, mock_metadata_class):
        mock_meta_instance = Mock()
        mock_meta_data = {"test": "data"}
        mock_meta_instance.determine_metadata.return_value = mock_meta_data
        mock_metadata_class.return_value = mock_meta_instance

        request = self.factory.get("/api/test/api_schema/")
        django_request = Request(request)

        response = self.viewset.api_schema(django_request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, mock_meta_data)


class MetadataTestCase(TestCase):
    def setUp(self):
        self.metadata = Metadata()
        self.factory = RequestFactory()
