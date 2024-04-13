from __future__ import annotations

from django.utils.decorators import method_decorator
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import conditional_cache_page
from product.models.category import ProductCategory
from product.serializers.category import ProductCategorySerializer

DEFAULT_PRODUCT_CATEGORY_CACHE_TTL = 60 * 60 * 2


class ProductCategoryViewSet(BaseModelViewSet):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id"]
    ordering_fields = [
        "id",
        "created_at",
    ]
    ordering = ["-created_at"]
    search_fields = ["id"]

    @method_decorator(conditional_cache_page(DEFAULT_PRODUCT_CATEGORY_CACHE_TTL))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_PRODUCT_CATEGORY_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        return super().retrieve(request, pk=pk, *args, **kwargs)
