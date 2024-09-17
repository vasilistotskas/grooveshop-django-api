from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from vat.models import Vat
from vat.serializers import VatSerializer


class VatViewSet(BaseModelViewSet):
    queryset = Vat.objects.all()
    serializer_class = VatSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "value"]
    ordering_fields = ["id", "value", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id", "value"]
