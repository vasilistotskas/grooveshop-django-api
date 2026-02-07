from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view


from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.utils.views import cache_methods
from pay_way.filters import PayWayFilter
from pay_way.models import PayWay
from pay_way.serializers import (
    PayWayDetailSerializer,
    PayWaySerializer,
    PayWayWriteSerializer,
)

serializers_config: SerializersConfig = {
    **crud_config(
        list=PayWaySerializer,
        detail=PayWayDetailSerializer,
        write=PayWayWriteSerializer,
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=PayWay,
        display_config={
            "tag": "Payment methods",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class PayWayViewSet(BaseModelViewSet):
    queryset = PayWay.objects.all()
    serializers_config = serializers_config
    filterset_class = PayWayFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "cost",
        "free_threshold",
        "provider_code",
        "is_online_payment",
        "requires_confirmation",
        "sort_order",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "provider_code",
        "translations__name",
        "translations__description",
        "translations__instructions",
    ]

    def get_queryset(self):
        if self.action == "list":
            return PayWay.objects.for_list()
        return PayWay.objects.for_detail()
