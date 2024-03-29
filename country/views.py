from __future__ import annotations

from django.utils.decorators import method_decorator
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import conditional_cache_page
from country.models import Country
from country.serializers import CountrySerializer

DEFAULT_COUNTRY_CACHE_TTL = 60 * 60 * 2


class CountryViewSet(BaseModelViewSet):
    queryset = Country.objects.all()
    serializer_class = CountrySerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = [
        "alpha_2",
        "alpha_3",
        "translations__name",
        "iso_cc",
        "phone_code",
    ]
    ordering_fields = ["alpha_2", "-created_at"]
    ordering = ["-created_at"]
    search_fields = [
        "alpha_2",
        "alpha_3",
        "iso_cc",
        "phone_code",
    ]

    @method_decorator(conditional_cache_page(DEFAULT_COUNTRY_CACHE_TTL))
    def list(self, request, *args, **kwargs) -> Response:
        return super().list(request, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_COUNTRY_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        return super().retrieve(request, pk=pk, *args, **kwargs)
