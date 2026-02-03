from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view
from rest_framework.permissions import IsAdminUser

from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    create_schema_view_config,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods
from product.filters.attribute import AttributeFilter
from product.models.attribute import Attribute
from product.serializers.attribute import AttributeSerializer

res_serializers: ResponseSerializersConfig = {
    "create": AttributeSerializer,
    "list": AttributeSerializer,
    "retrieve": AttributeSerializer,
    "update": AttributeSerializer,
    "partial_update": AttributeSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Attribute,
        display_config={
            "tag": "Product Attributes",
        },
        response_serializers=res_serializers,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class AttributeViewSet(BaseModelViewSet):
    """
    ViewSet for managing product attributes.

    Product attributes define attribute types (e.g., Size, Color, Capacity) that can be
    assigned to products. Each attribute can have multiple values and supports multi-language
    translations.
    """

    queryset = Attribute.objects.all()
    serializer_class = AttributeSerializer
    filterset_class = AttributeFilter
    response_serializers = res_serializers
    ordering_fields = [
        "id",
        "sort_order",
        "created_at",
        "updated_at",
    ]
    ordering = ["sort_order"]
    search_fields = ["translations__name"]

    def get_queryset(self):
        """
        Return optimized queryset with annotations.

        Includes values_count and usage_count annotations for efficient display.
        Uses prefetch_related for translations to minimize database queries.
        """
        return (
            super()
            .get_queryset()
            .with_values_count()
            .with_usage_count()
            .prefetch_related("translations")
        )

    def get_permissions(self):
        """
        Set permissions based on action.

        - Admin only for write operations (create, update, partial_update, destroy)
        - Public read access for list and retrieve
        """
        if self.action in ["create", "update", "partial_update", "destroy"]:
            self.permission_classes = [IsAdminUser]
        else:
            self.permission_classes = []

        return super().get_permissions()
