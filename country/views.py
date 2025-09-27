from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view


from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods
from country.filters import CountryFilter
from country.models import Country
from country.serializers import (
    CountryDetailSerializer,
    CountrySerializer,
    CountryWriteSerializer,
)

req_serializers: RequestSerializersConfig = {
    "create": CountryWriteSerializer,
    "update": CountryWriteSerializer,
    "partial_update": CountryWriteSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": CountryDetailSerializer,
    "list": CountrySerializer,
    "retrieve": CountryDetailSerializer,
    "update": CountryDetailSerializer,
    "partial_update": CountryDetailSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Country,
        display_config={
            "tag": "Countries",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class CountryViewSet(BaseModelViewSet):
    queryset = Country.objects.all()
    response_serializers = res_serializers
    request_serializers = req_serializers
    filterset_class = CountryFilter
    ordering_fields = [
        "alpha_2",
        "alpha_3",
        "iso_cc",
        "phone_code",
        "created_at",
        "updated_at",
        "sort_order",
        "translations__name",
    ]
    ordering = ["sort_order", "translations__name"]
    search_fields = [
        "alpha_2",
        "alpha_3",
        "iso_cc",
        "phone_code",
        "translations__name",
    ]
