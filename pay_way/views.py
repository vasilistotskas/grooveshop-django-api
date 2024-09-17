from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods
from pay_way.models import PayWay
from pay_way.serializers import PayWaySerializer


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class PayWayViewSet(BaseModelViewSet):
    queryset = PayWay.objects.all()
    serializer_class = PayWaySerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["active", "cost", "free_for_order_amount"]
    ordering_fields = [
        "cost",
        "free_for_order_amount",
        "created_at",
    ]
    ordering = ["-created_at"]
    search_fields = []
