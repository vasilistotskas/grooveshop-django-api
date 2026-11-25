"""Public read-only views for the shipping abstraction.

* ``GET /api/v1/shipping/options`` — the matrix of (provider, kind)
  rows the checkout UI renders.
* ``GET /api/v1/shipping/free-shipping-info`` — per-carrier free-
  shipping thresholds + aggregate min/max for the storefront's
  "Δωρεάν μεταφορικά άνω των X €" line on the PDP and cart summary.
* ``GET /api/v1/shipping/policy`` — the store's standing shipping
  policy (one row per provider × kind × country it offers), for the
  storefront's Organization structured data.
* ``GET /api/v1/shipping/product/<product_id>`` — the cheapest
  delivery of one product bought on its own, for the product page's
  "shipping from X" line.
* ``GET /api/v1/shipping/providers`` — admin-only list of provider rows
  for diagnostics.
"""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cart.services import cart_item_pairs
from cart.views.cart import CART_TOKEN_HEADERS
from core.api.permissions import StoreStaffModelPermissions
from core.api.serializers import ErrorResponseSerializer
from core.utils.views import cache_methods
from product.models.product import Product
from shipping.models import ShippingProvider
from shipping.serializers import (
    FreeShippingInfoQuerySerializer,
    FreeShippingInfoSerializer,
    ProductShippingSerializer,
    ShippingOptionSerializer,
    ShippingOptionsQuerySerializer,
    ShippingPolicyEntrySerializer,
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
                description=(
                    "ISO 3166-1 alpha-2 country code (e.g. 'GR'). "
                    "Required — a ``ShippingRate`` belongs to a zone of "
                    "one country."
                ),
                required=True,
                type=str,
            ),
            OpenApiParameter(
                name="region_id",
                description=(
                    "The delivery region's code, once chosen. A home "
                    "delivery is priced by the shipping zone of the "
                    "region; without one, by the zone covering the whole "
                    "country."
                ),
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
            *CART_TOKEN_HEADERS,
        ],
        responses={
            200: ShippingOptionSerializer(many=True),
            # A missing or malformed query parameter, and an
            # ``X-Cart-Token`` that is unknown, expired or revoked (never
            # answered with another cart).
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
        tags=["Shipping"],
    )
    def get(self, request: Request) -> Response:
        query = ShippingOptionsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        # Required on the serializer — a ``ShippingRate`` belongs to a
        # zone of one country, so there is no destination-agnostic matrix.
        country_code = query.validated_data["country_code"]
        amount = float(query.validated_data.get("order_value_amount") or 0)
        currency = query.validated_data.get("currency") or "EUR"
        weight_grams = query.validated_data.get("weight_grams")
        region_id = query.validated_data.get("region_id") or None

        # The caller's cart, found the way every cart endpoint finds it
        # (``X-Cart-Id`` / the signed-in user). None when it names none.
        items = cart_item_pairs(request)

        options = ShippingService.available_options(
            country_code=country_code,
            order_value_amount=amount,
            currency=currency,
            weight_grams=weight_grams,
            items=items,
            region_id=region_id,
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


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["get"])
class ShippingPolicyView(APIView):
    """The store's standing shipping policy.

    Read-only and public. Built from the same eligibility as the
    checkout's options (``ShippingService.offered_rates``) but with no
    cart, weight or order value, so every shopper gets the same answer
    and it is cached like the other public catalogue reads.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="getShippingPolicy",
        summary="The store's shipping policy",
        description=(
            "One row per (provider, kind, country) the store actually "
            "ships: the rate's price, free-shipping threshold, delivery "
            "estimate and weight cap. Eligibility is the checkout's own, "
            "so a row appears here exactly when checkout would offer it "
            "to that country."
        ),
        responses=ShippingPolicyEntrySerializer(many=True),
        tags=["Shipping"],
    )
    def get(self, request: Request) -> Response:
        serializer = ShippingPolicyEntrySerializer(
            ShippingService.shipping_policy(), many=True
        )
        return Response(serializer.data)


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["get"])
class ProductShippingView(APIView):
    """The cheapest delivery of one product bought on its own.

    Read-only and public, for a shopper not known yet, so every caller
    gets the same answer and it is cached like the other public
    catalogue reads. The ``shipping``, ``products`` and ``promotions``
    cache surfaces purge it: a rate, the product (price, weight, size)
    and a promotion each move it.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="getProductShipping",
        summary="What shipping one product costs",
        description=(
            "The cheapest delivery checkout would charge a cart holding "
            "this product alone, to the store's first shipping country: "
            "after the free-shipping threshold and the automatic "
            "promotions such a cart gets, leaving out every delivery "
            "that cannot carry the product (weight cap, parcel size). "
            "``estimate`` is null when the store ships nowhere or no "
            "delivery can take the product. Returns 404 when the "
            "product does not exist or is not active."
        ),
        responses={
            200: ProductShippingSerializer,
            404: ErrorResponseSerializer,
        },
        tags=["Shipping"],
    )
    def get(self, request: Request, product_id: int) -> Response:
        product = get_object_or_404(
            Product.objects.filter(active=True).select_related("vat"),
            pk=product_id,
        )
        serializer = ProductShippingSerializer(
            {"estimate": ShippingService.product_shipping_from(product)}
        )
        return Response(serializer.data)


class ShippingProviderListView(generics.ListAPIView):
    """Admin-only list of registered providers for diagnostics."""

    permission_classes = [StoreStaffModelPermissions]
    serializer_class = ShippingProviderSerializer
    queryset = ShippingProvider.objects.all().order_by("priority", "name")
    pagination_class = None
