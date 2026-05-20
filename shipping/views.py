"""Public read-only views for the shipping abstraction.

* ``GET /api/v1/shipping/options`` — the matrix of (provider, kind)
  rows the checkout UI renders.
* ``GET /api/v1/shipping/free-shipping-info`` — per-carrier free-
  shipping thresholds + aggregate min/max for the storefront's
  "Δωρεάν μεταφορικά άνω των X €" line on the PDP and cart summary.
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
    FreeShippingInfoQuerySerializer,
    FreeShippingInfoSerializer,
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
            OpenApiParameter(
                name="weight_grams",
                description=(
                    "Total cart weight in grams. Threaded into the ACS "
                    "live quote so the displayed price matches the "
                    "weight-banded tariff bracket the voucher mint will "
                    "charge. Optional; defaults to ACS's 500g minimum."
                ),
                required=False,
                type=int,
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
        weight_grams = query.validated_data.get("weight_grams")

        options = ShippingService.available_options(
            country_code=country_code,
            order_value_amount=amount,
            currency=currency,
            weight_grams=weight_grams,
        )
        serializer = ShippingOptionSerializer(options, many=True)
        return Response(serializer.data)


class FreeShippingInfoView(APIView):
    """Aggregate per-carrier free-shipping thresholds.

    Read-only and public — the storefront calls this on the PDP and
    cart page to render "Δωρεάν μεταφορικά άνω των X €". Decouples the
    marketing copy from per-carrier ``extra_settings`` keys: the
    frontend reads ONE consistent shape, and adding a new carrier
    flows through the same hook that drives checkout pricing.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="getFreeShippingInfo",
        summary="Free-shipping thresholds for the storefront",
        description=(
            "Returns per-(active provider × kind) free-shipping "
            "thresholds plus aggregate min/max. The storefront uses "
            "``min_threshold`` as the headline 'from X €' number on "
            "the PDP and cart summary."
        ),
        parameters=[
            OpenApiParameter(
                name="country_code",
                description=(
                    "Optional ISO 3166-1 alpha-2 filter. Carriers "
                    "with a ``metadata['supported_countries']`` list "
                    "that excludes the code are dropped."
                ),
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="currency",
                description=(
                    "Currency the response should advertise. Defaults "
                    "to settings.DEFAULT_CURRENCY (EUR)."
                ),
                required=False,
                type=str,
            ),
        ],
        responses=FreeShippingInfoSerializer,
        tags=["Shipping"],
    )
    def get(self, request: Request) -> Response:
        query = FreeShippingInfoQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        country_code = query.validated_data.get("country_code") or None
        currency = query.validated_data.get("currency") or None

        info = ShippingService.free_shipping_info(
            currency=currency,
            country_code=country_code,
        )
        serializer = FreeShippingInfoSerializer(info)
        return Response(serializer.data)


class ShippingProviderListView(generics.ListAPIView):
    """Admin-only list of registered providers for diagnostics."""

    permission_classes = [IsAdminUser]
    serializer_class = ShippingProviderSerializer
    queryset = ShippingProvider.objects.all().order_by("priority", "name")
    pagination_class = None
