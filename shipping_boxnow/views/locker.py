"""BoxNow locker viewset — public read-only endpoints.

Lockers are public data (no auth required).  The `nearest` action
proxies the BoxNow address-delivery-check API to find the closest
locker for a given address.
"""

from __future__ import annotations

import logging

from django.conf import settings
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
from shipping_boxnow.models import BoxNowLocker
from shipping_boxnow.serializers import (
    BoxNowLockerDetailSerializer,
    BoxNowLockerSerializer,
    BoxNowNearestLockerRequestSerializer,
    BoxNowNearestLockerResponseSerializer,
)

logger = logging.getLogger(__name__)

serializers_config: SerializersConfig = {
    **crud_config(
        list=BoxNowLockerSerializer,
        detail=BoxNowLockerDetailSerializer,
    ),
    "nearest": ActionConfig(
        request=BoxNowNearestLockerRequestSerializer,
        response=BoxNowNearestLockerResponseSerializer,
        summary="Find nearest BoxNow locker",
        description=(
            "Accepts a delivery address and returns the closest"
            " available BoxNow locker by calling the BoxNow"
            " address-delivery-check API."
        ),
        tags=["BoxNow lockers"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=BoxNowLocker,
        display_config={
            "tag": "BoxNow lockers",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
        include_language_param=False,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BoxNowLockerViewSet(BaseModelViewSet):
    """Read-only viewset for BoxNow locker locations."""

    queryset = BoxNowLocker.objects.filter(is_active=True)
    serializers_config = serializers_config
    permission_classes = [AllowAny]
    search_fields = [
        "external_id",
        "name",
        "title",
        "address_line_1",
        "postal_code",
    ]
    ordering_fields = [
        "external_id",
        "postal_code",
        "last_synced_at",
        "created_at",
    ]
    ordering = ["postal_code", "external_id"]

    # Disable write actions — this viewset is read-only.
    http_method_names = ["get", "post", "head", "options"]

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        url_path="nearest",
    )
    def nearest(self, request):
        """Find the nearest BoxNow locker for a delivery address.

        Delegates to ``BoxNowClient.find_closest_locker()`` which calls
        the BoxNow ``checkAddressDelivery`` endpoint.  Returns a single
        locker object (external ID, name, address) on success.
        """
        from shipping_boxnow.client import BoxNowClient
        from shipping_boxnow.exceptions import BoxNowAPIError

        serializer = BoxNowNearestLockerRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        vd = serializer.validated_data
        try:
            locker = BoxNowClient().find_closest_locker(
                city=vd["city"],
                street=vd["street"],
                postal_code=vd["postal_code"],
                region=vd.get("region", "el-GR"),
                compartment_size=vd.get("compartment_size", 1),
            )
        except BoxNowAPIError as exc:
            logger.warning(
                "BoxNow nearest-locker API error | code=%s | msg=%s",
                exc.code,
                exc,
            )
            return Response({"detail": str(exc), "code": exc.code}, status=400)

        response_serializer = BoxNowNearestLockerResponseSerializer(locker)
        return Response(response_serializer.data, status=200)
