"""Read-only viewset for the AcsStation cache.

Phase 2 — drives the Smartpoint locker picker on the Nuxt checkout.
Public, cached.  All writes happen via the daily ``sync_acs_stations``
Celery task.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from core.utils.views import cache_methods
from shipping_acs.enum.shop_kind import AcsShopKind
from shipping_acs.models import AcsStation
from shipping_acs.serializers import (
    AcsStationDetailSerializer,
    AcsStationSerializer,
)

# Smartpoint locker kinds — used as the default for the pickup-point
# checkout flow (kinds 7 + 8 in the GR catalogue per ACS PDF "ΣΤΑΘΜΟΙ ACS").
_LOCKER_KINDS: tuple[int, ...] = (
    AcsShopKind.SMARTPOINT_INBOUND,
    AcsShopKind.SMARTPOINT_OUTBOUND,
)


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class AcsStationViewSet(viewsets.ReadOnlyModelViewSet):
    """List / retrieve ACS stations and Smartpoint lockers."""

    queryset = AcsStation.objects.filter(is_active=True)
    permission_classes = [AllowAny]
    lookup_field = "external_id"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "external_id",
        "name",
        "address_line_1",
        "postal_code",
        "city",
    ]
    ordering_fields = [
        "external_id",
        "postal_code",
        "last_synced_at",
    ]
    ordering = ["postal_code", "external_id"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AcsStationDetailSerializer
        return AcsStationSerializer

    def get_queryset(self):
        """Filter by ?shopKind / ?postalCode / ?countryCode if supplied.

        Defaults to locker kinds (7+8) so the checkout picker doesn't
        get flooded with general shop rows that aren't valid pickup
        destinations.

        Note: ``CamelCaseMiddleWare`` (settings.MIDDLEWARE) rewrites
        camelCase query keys to snake_case before the view sees them,
        so we tolerate either spelling — direct API callers and the
        Nuxt proxy both work.
        """
        qs = super().get_queryset()

        kind_param = self._read_param("shopKind", "shop_kind")
        if kind_param:
            try:
                qs = qs.filter(shop_kind=int(kind_param))
            except ValueError:
                pass
        elif self.action == "list":
            # Default the list endpoint to lockers only — admin /
            # diagnostics callers can override with shopKind=1, 4, etc.
            qs = qs.filter(shop_kind__in=_LOCKER_KINDS)

        postal = self._read_param("postalCode", "postal_code")
        if postal:
            qs = qs.filter(postal_code__startswith=postal[:5])

        country = self._read_param("countryCode", "country_code")
        if country:
            qs = qs.filter(country_code=country.upper())

        return qs

    def _read_param(self, *aliases: str) -> str | None:
        """Return the first non-empty value among ``aliases``.

        Tolerates both camelCase and snake_case keys so the endpoint
        works the same whether the caller hits Django directly or via
        the camel-case middleware path.
        """
        for alias in aliases:
            value = self.request.query_params.get(alias)
            if value:
                return value
        return None

    @extend_schema(
        operation_id="findNearestAcsStations",
        summary="Find nearest ACS Smartpoint lockers to a postcode",
        description=(
            "Returns up to 20 active locker rows whose ``postal_code`` "
            "matches the supplied postcode prefix, ordered by exact "
            "match first then alphabetic.  Falls back to a city-name "
            "ILIKE match when no postcode hits are found.  Designed "
            "for the checkout locker picker — no GPS or geo math, the "
            "Nuxt UI does the visual ordering."
        ),
        parameters=[
            OpenApiParameter(
                name="postalCode",
                description="Greek postcode (5-digit), required.",
                required=True,
                type=str,
            ),
            OpenApiParameter(
                name="city",
                description="Optional city-name fallback.",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="shopKind",
                description="Optional override (default: lockers 7+8).",
                required=False,
                type=int,
            ),
        ],
        responses={200: AcsStationSerializer(many=True)},
        tags=["ACS stations"],
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="nearest",
        permission_classes=[AllowAny],
    )
    def nearest(self, request: Request) -> Response:
        postal = (self._read_param("postalCode", "postal_code") or "").strip()
        city = (self._read_param("city") or "").strip()
        kind_param = self._read_param("shopKind", "shop_kind")

        qs = AcsStation.objects.filter(is_active=True)
        if kind_param:
            try:
                qs = qs.filter(shop_kind=int(kind_param))
            except ValueError:
                qs = qs.filter(shop_kind__in=_LOCKER_KINDS)
        else:
            qs = qs.filter(shop_kind__in=_LOCKER_KINDS)

        if not postal:
            return Response(
                {"detail": "postalCode query parameter is required."},
                status=400,
            )

        # Postcode prefix match first — ACS Smartpoints share the
        # 5-digit catchment with deliveries to that area.
        prefix = postal[:5]
        rows = list(
            qs.filter(postal_code__startswith=prefix).order_by(
                "postal_code", "external_id"
            )[:20]
        )

        # Fallback to city-name ILIKE when no postcode hits — covers
        # rural / island postcodes that don't map cleanly to a locker.
        if not rows and city:
            rows = list(
                qs.filter(
                    Q(city__icontains=city) | Q(name__icontains=city)
                ).order_by("postal_code", "external_id")[:20]
            )

        serializer = AcsStationSerializer(
            rows, many=True, context={"request": request}
        )
        return Response(serializer.data)
