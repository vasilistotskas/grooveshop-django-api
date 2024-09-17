from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods
from country.models import Country
from country.serializers import CountrySerializer


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class CountryViewSet(BaseModelViewSet):
    queryset = Country.objects.all()
    serializer_class = CountrySerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = [
        "alpha_2",
        "alpha_3",
        "translations__name",
        "iso_cc",
        "phone_code",
    ]
    ordering_fields = ["alpha_2", "created_at"]
    ordering = ["-created_at"]
    search_fields = [
        "alpha_2",
        "alpha_3",
        "iso_cc",
        "phone_code",
    ]
