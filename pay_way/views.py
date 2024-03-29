from __future__ import annotations

from django.utils.decorators import method_decorator
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import conditional_cache_page
from pay_way.models import PayWay
from pay_way.serializers import PayWaySerializer

DEFAULT_PAY_WAY_CACHE_TTL = 60 * 60 * 2


class PayWayViewSet(BaseModelViewSet):
    queryset = PayWay.objects.all()
    serializer_class = PayWaySerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["active", "cost", "free_for_order_amount"]
    ordering_fields = [
        "cost",
        "free_for_order_amount",
        "created_at",
    ]
    ordering = ["-created_at"]
    search_fields = []

    @method_decorator(conditional_cache_page(DEFAULT_PAY_WAY_CACHE_TTL))
    def list(self, request, *args, **kwargs) -> Response:
        return super().list(request, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_PAY_WAY_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        return super().retrieve(request, pk=pk, *args, **kwargs)
