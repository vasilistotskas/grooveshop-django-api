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
from country.filters import CountryFilter
from country.models import Country
from country.serializers import (
    CountryDetailSerializer,
    CountrySerializer,
    CountryWriteSerializer,
)

serializers_config: SerializersConfig = {
    **crud_config(
        list=CountrySerializer,
        detail=CountryDetailSerializer,
        write=CountryWriteSerializer,
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Country,
        display_config={
            "tag": "Countries",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class CountryViewSet(BaseModelViewSet):
    queryset = Country.objects.all()
    serializers_config = serializers_config
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

    def get_queryset(self):
        """
        Return optimized queryset based on action.

        Uses Country.objects.for_list() for list views and
        Country.objects.for_detail() for detail views.
        """
        if self.action == "list":
            return Country.objects.for_list()
        return Country.objects.for_detail()
