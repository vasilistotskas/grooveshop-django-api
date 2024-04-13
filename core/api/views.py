from django.conf import settings
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.metadata import SimpleMetadata
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.pagination.cursor import CursorPaginator
from core.pagination.limit_offset import LimitOffsetPaginator
from core.pagination.page_number import PageNumberPaginator
from core.utils.views import conditional_cache_page
from core.utils.views import TranslationsProcessingMixin

default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
API_SCHEMA_CACHE_TTL = 60 * 60 * 2


class Metadata(SimpleMetadata):
    def determine_metadata(self, request, view):
        metadata = super().determine_metadata(request, view)
        metadata["filterset_fields"] = getattr(view, "filterset_fields", [])
        metadata["ordering_fields"] = getattr(view, "ordering_fields", [])
        metadata["ordering"] = getattr(view, "ordering", [])
        metadata["search_fields"] = getattr(view, "search_fields", [])
        return metadata


class ExpandModelViewSet(ModelViewSet):
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["expand"] = self.request.query_params.get("expand", "false").lower()
        context["expand_fields"] = self.request.query_params.get("expand_fields", "")
        return context


class PaginationModelViewSet(ModelViewSet):
    @property
    def paginator(self):
        if not hasattr(self, "_paginator"):
            if self.pagination_class is None:
                self._paginator = None
            else:
                pagination_type = self.request.query_params.get(
                    "pagination_type", ""
                ).lower()
                paginator_mapping = {
                    "page_number": PageNumberPaginator,
                    "limit_offset": LimitOffsetPaginator,
                    "cursor": CursorPaginator,
                }
                self._paginator = paginator_mapping.get(
                    pagination_type, self.pagination_class
                )()

        return self._paginator

    def paginate_and_serialize(self, queryset, request, many=True):
        pagination_param = request.query_params.get("pagination", "true").lower()
        if pagination_param == "false":
            serializer = self.get_serializer(
                queryset, many=many, context=self.get_serializer_context()
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(
                page, many=many, context=self.get_serializer_context()
            )
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(
            queryset, many=many, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)


class TranslationsModelViewSet(TranslationsProcessingMixin, ModelViewSet):
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["language"] = self.request.query_params.get(
            "language", default_language
        )
        return context

    def create(self, request, *args, **kwargs):
        request = self.process_translations_data(request)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        request = self.process_translations_data(request)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)


class BaseModelViewSet(
    ExpandModelViewSet, TranslationsModelViewSet, PaginationModelViewSet
):
    metadata_class = Metadata

    @method_decorator(conditional_cache_page(API_SCHEMA_CACHE_TTL))
    @action(detail=False, methods=["GET"])
    def api_schema(self, request, *args, **kwargs):
        meta = self.metadata_class()
        data = meta.determine_metadata(request, self)
        return Response(data)
