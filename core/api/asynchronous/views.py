from asyncio import iscoroutine

from adrf.viewsets import ViewSet as AsyncAPIViewSet
from django.conf import settings
from django.db.models import QuerySet
from django.http import Http404
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.metadata import SimpleMetadata
from rest_framework.response import Response

from core.pagination.asynchronous.page_number import AsyncPageNumberPaginator
from core.pagination.cursor import CursorPaginator
from core.pagination.limit_offset import LimitOffsetPaginator
from core.pagination.page_number import PageNumberPaginator
from core.utils.views import conditional_cache_page
from core.utils.views import TranslationsProcessingMixin

default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
API_SCHEMA_CACHE_TTL = 60 * 60 * 2


def _get_queryset(klass):
    if hasattr(klass, "_default_manager"):
        return klass._default_manager.all()
    return klass


async def aget_object_or_404(klass, *args, **kwargs):
    queryset = _get_queryset(klass)
    if not hasattr(queryset, "aget"):
        klass__name = (
            klass.__name__ if isinstance(klass, type) else klass.__class__.__name__
        )
        raise ValueError(
            "First argument to aget_object_or_404() must be a Model, Manager, or "
            f"QuerySet, not '{klass__name}'."
        )
    try:
        return await queryset.aget(*args, **kwargs)
    except queryset.model.DoesNotExist:
        raise Http404(f"No {queryset.model._meta.object_name} matches the given query.")


class Metadata(SimpleMetadata):
    def determine_metadata(self, request, view):
        metadata = super().determine_metadata(request, view)
        metadata["filterset_fields"] = getattr(view, "filterset_fields", [])
        metadata["ordering_fields"] = getattr(view, "ordering_fields", [])
        metadata["ordering"] = getattr(view, "ordering", [])
        metadata["search_fields"] = getattr(view, "search_fields", [])
        return metadata


class AsyncBaseAPIViewSet(
    TranslationsProcessingMixin,
    AsyncAPIViewSet,
):
    view_is_async = True
    metadata_class = Metadata
    filter_backends = settings.REST_FRAMEWORK.get("DEFAULT_FILTER_BACKENDS")
    pagination_class = AsyncPageNumberPaginator
    serializer_class = None
    queryset = None
    lookup_field = "pk"
    lookup_url_kwarg = None

    def __class_getitem__(cls, *args, **kwargs):
        return cls

    @method_decorator(conditional_cache_page(API_SCHEMA_CACHE_TTL))
    @action(detail=False, methods=["GET"])
    def api_schema(self, request, *args, **kwargs):
        meta = self.metadata_class()
        data = meta.determine_metadata(request, self)
        return Response(data)

    @property
    def paginator(self) -> AsyncPageNumberPaginator:
        if not hasattr(self, "_paginator"):
            if self.pagination_class is None:
                self._paginator = None
            else:
                pagination_type = self.request.query_params.get(
                    "pagination_type", ""
                ).lower()
                paginator_mapping = {
                    "page_number": AsyncPageNumberPaginator,
                }
                self._paginator = paginator_mapping.get(
                    pagination_type, self.pagination_class
                )()

        return self._paginator

    def get_queryset(self):
        assert self.queryset is not None, (
            "'%s' should either include a `queryset` attribute, "
            "or override the `get_queryset()` method." % self.__class__.__name__
        )

        queryset = self.queryset
        if isinstance(queryset, QuerySet):
            queryset = queryset.all()
        return queryset

    def filter_queryset(self, queryset):
        for backend in list(self.filter_backends):
            queryset = backend().filter_queryset(self.request, queryset, self)
        return queryset

    async def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())

        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field

        assert lookup_url_kwarg in self.kwargs, (
            "Expected view %s to be called with a URL keyword argument "
            'named "%s". Fix your URL conf, or set the `.lookup_field` '
            "attribute on the view correctly."
            % (self.__class__.__name__, lookup_url_kwarg)
        )

        filter_kwargs = {self.lookup_field: self.kwargs[lookup_url_kwarg]}
        obj = await aget_object_or_404(queryset, **filter_kwargs)

        self.check_object_permissions(self.request, obj)

        return obj

    def get_success_headers(self, data):
        try:
            return {
                "Location": str(
                    data[settings.REST_FRAMEWORK.get("URL_FIELD_NAME", "url")]
                )
            }
        except (TypeError, KeyError):
            return {}

    async def paginate_queryset(self, queryset):
        if self.paginator is None:
            return None
        return await self.paginator.paginate_queryset(queryset, self.request, view=self)

    async def perform_create(self, serializer):
        await serializer.asave()

    async def perform_update(self, serializer):
        await serializer.asave()

    async def perform_destroy(self, instance):
        await instance.adelete()

    async def get_paginated_response(self, data):
        assert self.paginator is not None
        return await self.paginator.get_paginated_response(data)

    async def paginate_and_serialize(self, queryset, request, many=True):
        pagination_param = request.query_params.get("pagination", "true").lower()

        if iscoroutine(queryset):
            queryset = await queryset

        if pagination_param == "false":
            serializer = self.get_serializer(
                queryset, many=many, context=self.get_serializer_context()
            )
            async_data = await serializer.adata
            return Response(async_data, status=status.HTTP_200_OK)

        page = await self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(
                page, many=many, context=self.get_serializer_context()
            )
            async_data = await serializer.adata
            return await self.get_paginated_response(async_data)

        serializer = self.get_serializer(
            queryset, many=many, context=self.get_serializer_context()
        )
        async_data = await serializer.adata
        return Response(async_data, status=status.HTTP_200_OK)

    def get_serializer_class(self):
        assert self.serializer_class is not None, (
            "'%s' should either include a `serializer_class` attribute, "
            "or override the `get_serializer_class()` method." % self.__class__.__name__
        )

        return self.serializer_class

    def get_serializer(self, *args, **kwargs):
        serializer_class = self.get_serializer_class()
        kwargs.setdefault("context", self.get_serializer_context())
        return serializer_class(*args, **kwargs)

    def get_serializer_context(self):
        return {
            "request": self.request,
            "format": self.format_kwarg,
            "view": self,
            "language": self.request.query_params.get("language", default_language),
            "expand": self.request.query_params.get("expand", "false").lower(),
            "expand_fields": self.request.query_params.get("expand_fields", ""),
        }

    async def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        return await self.paginate_and_serialize(queryset, request)

    async def create(self, request, *args, **kwargs):
        request = self.process_translations_data(request)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        await self.perform_create(serializer)
        async_data = await serializer.adata
        headers = self.get_success_headers(async_data)
        return Response(async_data, status=status.HTTP_201_CREATED, headers=headers)

    async def retrieve(self, request, *args, **kwargs):
        instance = await self.get_object()
        serializer = self.get_serializer(instance)
        async_data = await serializer.adata
        return Response(async_data)

    async def update(self, request, *args, **kwargs):
        request = self.process_translations_data(request)
        partial = kwargs.pop("partial", False)
        instance = await self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        await self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        async_data = await serializer.adata
        return Response(async_data)

    async def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return await self.update(request, *args, **kwargs)

    async def destroy(self, request, *args, **kwargs):
        instance = await self.get_object()
        await self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)
