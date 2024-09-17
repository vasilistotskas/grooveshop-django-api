from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods
from tip.models import Tip
from tip.serializers import TipSerializer


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class TipViewSet(BaseModelViewSet):
    queryset = Tip.objects.all()
    serializer_class = TipSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "kind", "active"]
    ordering_fields = ["id", "kind", "active", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id", "translations__title"]
