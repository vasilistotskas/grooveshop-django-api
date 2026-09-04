from __future__ import annotations

import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from b2b.models import BusinessProfile
from b2b.serializers import (
    B2BErrorResponseSerializer,
    B2BPriceSerializer,
    BusinessProfileSerializer,
    BusinessProfileWriteSerializer,
)
from b2b.services import B2BPricingService, B2BService
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import B2BProfileSubmitThrottle
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from tenant.permissions import IsB2BEnabled, IsB2BWholesaleEnabled

logger = logging.getLogger(__name__)

MAX_PRICE_IDS = 100

serializers_config: SerializersConfig = {
    "profile": ActionConfig(
        response=BusinessProfileSerializer,
        responses={404: B2BErrorResponseSerializer},
        operation_id="getB2BProfile",
        summary=_("Get my business profile"),
        tags=["B2B"],
    ),
    "submit_profile": ActionConfig(
        request=BusinessProfileWriteSerializer,
        response=BusinessProfileSerializer,
        responses={400: B2BErrorResponseSerializer},
        operation_id="submitB2BProfile",
        summary=_("Submit or update my business profile"),
        tags=["B2B"],
    ),
    "prices": ActionConfig(
        response=B2BPriceSerializer,
        many=True,
        paginated=False,
        parameters=[
            OpenApiParameter(
                name="ids",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Comma-separated product ids (max 100)",
            ),
        ],
        operation_id="getB2BPrices",
        summary=_("Wholesale prices for a set of products"),
        tags=["B2B"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=BusinessProfile,
        display_config={"tag": "B2B"},
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class B2BViewSet(BaseModelViewSet):
    """Wholesale-program endpoints.

    Everything is authenticated and double-gated (plan flag + merchant
    runtime setting, 404 semantics) — a disabled program is
    indistinguishable from a nonexistent route, and the feature gates
    are evaluated before the auth state can leak.
    """

    queryset = BusinessProfile.objects.none()
    serializers_config = serializers_config
    permission_classes = [IsB2BEnabled, IsB2BWholesaleEnabled, IsAuthenticated]

    def get_throttles(self):
        if self.action == "submit_profile":
            # Each submit can trigger an outbound VIES HTTP check —
            # budget it tightly on top of the global daily caps.
            return [
                B2BProfileSubmitThrottle(),
                AnonRateThrottle(),
                UserRateThrottle(),
            ]
        return super().get_throttles()

    @action(detail=False, methods=["GET"])
    def profile(self, request):
        """GET /api/v1/b2b/profile — the caller's business profile."""
        profile = (
            BusinessProfile.objects.select_related("customer_group")
            .filter(user=request.user)
            .first()
        )
        if profile is None:
            return Response(
                {
                    "detail": _("No business profile yet."),
                    "reason": "no_business_profile",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        response_serializer_class = self.get_response_serializer()
        return Response(
            response_serializer_class(profile).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["PUT"], url_path="profile")
    def submit_profile(self, request):
        """PUT /api/v1/b2b/profile — create or update the profile.

        Identity edits on an APPROVED profile reset it to PENDING; the
        VIES snapshot refreshes on every submit (degrading gracefully
        when VIES is down).
        """
        request_serializer_class = self.get_request_serializer()
        serializer = request_serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = B2BService.submit_profile(
            request.user, serializer.validated_data
        )
        response_serializer_class = self.get_response_serializer()
        return Response(
            response_serializer_class(profile).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["GET"], pagination_class=None)
    def prices(self, request):
        """GET /api/v1/b2b/prices?ids=1,2,… — wholesale prices.

        Returns an EMPTY array (200) when the caller has no approved
        profile or assigned group — no information leak, and the client
        needs no special casing. Never cached anywhere.
        """
        raw_ids = (request.query_params.get("ids") or "").strip()
        try:
            product_ids = [
                int(chunk) for chunk in raw_ids.split(",") if chunk.strip()
            ]
        except ValueError:
            return Response(
                {"detail": _("Invalid ids parameter.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(product_ids) > MAX_PRICE_IDS:
            # Explicit 400 over silent truncation — a truncated response
            # would leave the client believing the missing ids priced
            # at retail.
            return Response(
                {
                    "detail": _(
                        "Too many ids — request at most {max} per call."
                    ).format(max=MAX_PRICE_IDS)
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        group = B2BService.resolve_group(request.user)
        payload = []
        if group is not None and product_ids:
            from product.models.product import Product

            products = Product.objects.select_related("vat").filter(
                pk__in=product_ids, active=True
            )
            resolved = B2BPricingService.resolve_map(products, group)
            for product in products:
                price = resolved[product.pk]
                # NET-based, matching CartItem.discount_percent — mixing
                # bases would show a different percent for the same
                # product between this endpoint and the cart.
                retail_net = product.price.amount
                effective_percent = (
                    (retail_net - price.net.amount) / retail_net * 100
                    if retail_net > 0
                    else 0
                )
                payload.append(
                    {
                        "product_id": product.pk,
                        "net_price": price.net.amount,
                        "final_price": price.final.amount,
                        "discount_percent": round(effective_percent, 2),
                    }
                )

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(payload, many=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)
