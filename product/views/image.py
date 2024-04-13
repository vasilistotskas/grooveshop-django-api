from __future__ import annotations

from django.utils.decorators import method_decorator
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import conditional_cache_page
from product.models.image import ProductImage
from product.serializers.image import ProductImageSerializer

DEFAULT_PRODUCT_IMAGE_CACHE_TTL = 60 * 60 * 2


class ProductImageViewSet(BaseModelViewSet):
    queryset = ProductImage.objects.all()
    serializer_class = ProductImageSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "product", "is_main"]
    ordering_fields = ["created_at", "-is_main"]
    ordering = ["-is_main", "-created_at"]

    @method_decorator(conditional_cache_page(DEFAULT_PRODUCT_IMAGE_CACHE_TTL))
    def list(self, request, *args, **kwargs) -> Response:
        return super().list(request, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_PRODUCT_IMAGE_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        return super().retrieve(request, pk=pk, *args, **kwargs)
