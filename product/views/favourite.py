from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from product.models.favourite import ProductFavourite
from product.serializers.favourite import ProductFavouriteSerializer


class ProductFavouriteViewSet(BaseModelViewSet):
    queryset = ProductFavourite.objects.all()
    serializer_class = ProductFavouriteSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "user_id", "product_id"]
    ordering_fields = [
        "id",
        "user_id",
        "product_id",
        "created_at",
    ]
    ordering = ["id"]
    search_fields = [
        "id",
        "user_id",
        "product_id",
    ]
