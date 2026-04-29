"""Public read-only views for the shipping abstraction.

* ``GET /api/v1/shipping/options`` — the matrix of (provider, kind)
  rows the checkout UI renders.
* ``GET /api/v1/shipping/providers`` — admin-only list of provider rows
  for diagnostics.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from shipping.models import ShippingProvider
from shipping.serializers import (
    ShippingOptionSerializer,
    ShippingOptionsQuerySerializer,
    ShippingProviderSerializer,
)
from shipping.services import ShippingService


class ShippingOptionsView(APIView):
    """Return the list of shipping options available at checkout."""

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="listShippingOptions",
        summary="List checkout shipping options",
        description=(
            "Returns one row per (active provider × supported kind) "
            "combination. The frontend renders one radio card per row."
        ),
        parameters=[
            OpenApiParameter(
                name="country_code",
                description="ISO 3166-1 alpha-2 country code (e.g. 'GR').",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="order_value_amount",
                description=(
                    "Cart subtotal — included so providers can return "
                    "free-shipping pricing when the threshold is hit."
                ),
                required=False,
                type=float,
            ),
            OpenApiParameter(
                name="currency",
                description="ISO 4217 currency code (default 'EUR').",
                required=False,
                type=str,
            ),
        ],
        responses=ShippingOptionSerializer(many=True),
        tags=["Shipping"],
    )
    def get(self, request: Request) -> Response:
        query = ShippingOptionsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        country_code = query.validated_data.get("country_code") or None
        amount = float(query.validated_data.get("order_value_amount") or 0)
        currency = query.validated_data.get("currency") or "EUR"

        options = ShippingService.available_options(
            country_code=country_code,
            order_value_amount=amount,
            currency=currency,
        )
        serializer = ShippingOptionSerializer(options, many=True)
        return Response(serializer.data)


class ShippingProviderListView(generics.ListAPIView):
    """Admin-only list of registered providers for diagnostics."""

    permission_classes = [IsAdminUser]
    serializer_class = ShippingProviderSerializer
    queryset = ShippingProvider.objects.all().order_by("priority", "name")
    pagination_class = None
