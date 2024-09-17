from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods
from product.models.image import ProductImage
from product.serializers.image import ProductImageSerializer


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductImageViewSet(BaseModelViewSet):
    queryset = ProductImage.objects.all()
    serializer_class = ProductImageSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "product", "is_main"]
    ordering_fields = ["created_at", "is_main"]
    ordering = ["-is_main", "-created_at"]
