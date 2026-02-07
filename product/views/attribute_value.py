from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view
from rest_framework.permissions import IsAdminUser

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from core.utils.views import cache_methods
from product.filters.attribute_value import AttributeValueFilter
from product.models.attribute_value import AttributeValue
from product.serializers.attribute_value import AttributeValueSerializer

serializers_config: SerializersConfig = {
    "list": ActionConfig(response=AttributeValueSerializer),
    "retrieve": ActionConfig(response=AttributeValueSerializer),
    "create": ActionConfig(response=AttributeValueSerializer),
    "update": ActionConfig(response=AttributeValueSerializer),
    "partial_update": ActionConfig(response=AttributeValueSerializer),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=AttributeValue,
        display_config={
            "tag": "Product Attributes",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class AttributeValueViewSet(BaseModelViewSet):
    """
    ViewSet for managing product attribute values.

    Attribute values are specific values for an attribute type (e.g., "Small", "Medium", "Large"
    for the "Size" attribute). Each value belongs to exactly one attribute and supports
    multi-language translations (Greek, English, German).
    """

    queryset = AttributeValue.objects.all()
    serializer_class = AttributeValueSerializer
    filterset_class = AttributeValueFilter
    serializers_config = serializers_config
    ordering_fields = [
        "id",
        "attribute",
        "sort_order",
        "created_at",
        "updated_at",
    ]
    ordering = ["attribute", "sort_order"]
    search_fields = ["translations__value", "attribute__translations__name"]

    def get_queryset(self):
        """
        Return optimized queryset with annotations.

        Includes usage_count annotation and prefetches related attribute
        and translations for efficient display. Uses select_related for
        the parent attribute to minimize database queries.
        """
        return (
            super()
            .get_queryset()
            .with_usage_count()
            .select_related("attribute")
            .prefetch_related("translations", "attribute__translations")
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
