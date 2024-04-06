from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.pagination.cursor import CursorPaginator
from core.pagination.limit_offset import LimitOffsetPaginator
from core.pagination.page_number import PageNumberPaginator
from core.utils.views import TranslationsProcessingMixin

default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


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

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        pagination_param = request.query_params.get("pagination", "true").lower()

        if pagination_param == "false":
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


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
    ExpandModelViewSet, PaginationModelViewSet, TranslationsModelViewSet
):
    pass
