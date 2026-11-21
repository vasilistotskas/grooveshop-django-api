from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.api.action_config import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.api.permissions import IsPlatformSuperuser
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.camel_case_ordering import ActionOrdering
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
    "all_countries": ActionConfig(
        response=CountrySerializer,
        many=True,
        paginated=False,
        operation_id="listAllCountries",
        summary=_("Every country, in one list"),
        description=_(
            "The whole country list as one array, filtered and ordered as "
            "the paginated list is: for a picker that needs every row."
        ),
        tags=["Countries"],
    ),
}


# The storefront's country pickers (shipping, phone prefix) need the
# whole list, about 250 rows, in one request.
UNPAGINATED_ACTIONS = frozenset({"list"})


@extend_schema_view(
    **create_schema_view_config(
        model_class=Country,
        display_config={
            "tag": "Countries",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
        unpaginated_actions=UNPAGINATED_ACTIONS,
    )
)
@cache_methods(
    settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve", "all_countries"]
)
class CountryViewSet(BaseModelViewSet):
    queryset = Country.objects.all()
    serializers_config = serializers_config
    unpaginated_actions = UNPAGINATED_ACTIONS
    filterset_class = CountryFilter

    def get_permissions(self):
        if self.action in (
            "create",
            "update",
            "partial_update",
            "destroy",
        ):
            return [IsPlatformSuperuser()]
        return [AllowAny()]

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
    # The whole list sorts as the paginated list does.
    action_ordering = {
        "all_countries": ActionOrdering(
            fields=tuple(ordering_fields), default=tuple(ordering)
        ),
    }

    def get_queryset(self):
        """
        Return optimized queryset based on action.

        Uses Country.objects.for_list() for list views and
        Country.objects.for_detail() for detail views.
        """
        if self.action in ("list", "all_countries"):
            return Country.objects.for_list()
        return Country.objects.for_detail()

    @action(detail=False, methods=["GET"], pagination_class=None)
    def all_countries(self, request):
        """GET /api/v1/country/all: the storefront's country pickers
        (shipping, phone prefix) need every row, about 250, in one
        request."""
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_response_serializer()(
            queryset, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data)
