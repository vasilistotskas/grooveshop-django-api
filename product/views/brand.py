from __future__ import annotations

from django.conf import settings
from django.db.models import Exists, OuterRef
from drf_spectacular.utils import extend_schema_view
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.utils.views import cache_methods
from product.models.brand import Brand
from product.models.product import Product
from product.serializers.brand import BrandSerializer

serializers_config: SerializersConfig = {
    **crud_config(list=BrandSerializer, detail=BrandSerializer),
    "all": ActionConfig(
        response=BrandSerializer,
        many=True,
        paginated=False,
        operation_id="listAllBrand",
        summary="List all brands (unpaginated)",
        description="Retrieve every brand that has an active product, "
        "without pagination. Used by the storefront to map the brand "
        "filter's ids to names.",
        tags=["Brands"],
        parameters=[],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Brand,
        display_config={"tag": "Brands"},
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve", "all"])
class BrandViewSet(BaseModelViewSet):
    """Public, read-only brand registry for the storefront's brand filter."""

    queryset = Brand.objects.all()
    serializers_config = serializers_config
    permission_classes = [AllowAny]
    ordering_fields = ["id", "name"]
    ordering = ["name"]
    search_fields = ["name"]

    def get_queryset(self):
        """Brands with at least one active product.

        Read actions are ``AllowAny``, so the queryset is the visibility
        gate: ``Brand`` is also the feed registry, and a brand whose
        products are all switched off, deleted or never created would
        otherwise be listed publicly with nothing behind it. The
        storefront's brand facet is built from active products, so
        every id it can show is still resolvable.
        """
        return Brand.objects.filter(
            Exists(Product.objects.active().filter(brand=OuterRef("pk")))
        ).order_by("name")

    @action(
        detail=False,
        methods=["GET"],
        url_path="all",
        pagination_class=None,
        filter_backends=[],
    )
    def all(self, request):
        """Return every visible brand without pagination."""
        serializer = BrandSerializer(
            self.get_queryset(),
            many=True,
            context=self.get_serializer_context(),
        )
        return Response(serializer.data)
