from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
from extra_settings.models import Setting
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from loyalty.filters.transaction import PointsTransactionFilter
from loyalty.models.tier import LoyaltyTier
from loyalty.models.transaction import PointsTransaction
from loyalty.serializers.loyalty import (
    LoyaltySummarySerializer,
    PointsTransactionSerializer,
    ProductPointsSerializer,
    RedeemPointsRequestSerializer,
    RedeemPointsResponseSerializer,
)
from loyalty.serializers.tier import LoyaltyTierSerializer
from loyalty.services import LoyaltyService
from product.models.product import Product

logger = logging.getLogger(__name__)

serializers_config: SerializersConfig = {
    "summary": ActionConfig(
        response=LoyaltySummarySerializer,
        operation_id="getLoyaltySummary",
        summary=_("Get loyalty summary"),
        tags=["Loyalty"],
    ),
    "transactions": ActionConfig(
        response=PointsTransactionSerializer,
        many=True,
        operation_id="listLoyaltyTransactions",
        summary=_("List loyalty transactions"),
        tags=["Loyalty"],
    ),
    "redeem": ActionConfig(
        request=RedeemPointsRequestSerializer,
        response=RedeemPointsResponseSerializer,
        operation_id="redeemLoyaltyPoints",
        summary=_("Redeem loyalty points"),
        tags=["Loyalty"],
    ),
    "product_points": ActionConfig(
        response=ProductPointsSerializer,
        operation_id="getProductLoyaltyPoints",
        summary=_("Get product loyalty points preview"),
        tags=["Loyalty"],
    ),
    "tiers": ActionConfig(
        response=LoyaltyTierSerializer,
        many=True,
        operation_id="listLoyaltyTiers",
        summary=_("List all loyalty tiers"),
        tags=["Loyalty"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=PointsTransaction,
        display_config={
            "tag": "Loyalty",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class LoyaltyViewSet(BaseModelViewSet):
    """ViewSet for loyalty system endpoints.

    Provides endpoints for viewing loyalty summary, transaction history,
    redeeming points, and previewing product points.
    All actions require authentication.
    """

    queryset = PointsTransaction.objects.none()
    permission_classes = [IsAuthenticated]
    serializers_config = serializers_config

    @action(detail=False, methods=["GET"])
    def summary(self, request):
        """GET /api/v1/loyalty/summary - User's loyalty summary."""
        user = request.user
        balance = LoyaltyService.get_user_balance(user)
        level = LoyaltyService.get_user_level(user)
        tier = LoyaltyService.get_user_tier(user)

        # Calculate points to next tier
        next_tier = LoyaltyTier.objects.get_next_tier(tier)
        points_to_next_tier = None
        if next_tier is not None:
            xp_per_level = Setting.get("LOYALTY_XP_PER_LEVEL", default=1000)
            if xp_per_level <= 0:
                xp_per_level = 1000
            xp_needed_for_next_tier = (
                next_tier.required_level - 1
            ) * xp_per_level
            points_to_next_tier = max(
                0, xp_needed_for_next_tier - user.total_xp
            )

        data = {
            "points_balance": balance,
            "total_xp": user.total_xp,
            "level": level,
            "tier": tier,
            "points_to_next_tier": points_to_next_tier,
        }

        response_serializer_class = self.get_response_serializer()
        serializer = response_serializer_class(
            data, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["GET"])
    def transactions(self, request):
        """GET /api/v1/loyalty/transactions - Paginated transaction history."""
        queryset = PointsTransaction.objects.for_list().for_user(request.user)

        # Apply filters
        filterset = PointsTransactionFilter(
            data=request.query_params, queryset=queryset, request=request
        )
        if filterset.is_valid():
            queryset = filterset.qs

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @action(detail=False, methods=["POST"])
    def redeem(self, request):
        """POST /api/v1/loyalty/redeem - Redeem points for discount."""
        request_serializer_class = self.get_request_serializer()
        serializer = request_serializer_class(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)

        points_amount = serializer.validated_data["points_amount"]
        currency = serializer.validated_data["currency"]
        order_id = serializer.validated_data.get("order_id")

        # Look up the order if order_id is provided
        order = None
        if order_id is not None:
            from order.models.order import Order

            try:
                order = Order.objects.get(id=order_id, user=request.user)
            except Order.DoesNotExist:
                return Response(
                    {"detail": _("Order not found or does not belong to you.")},
                    status=status.HTTP_404_NOT_FOUND,
                )

        try:
            discount = LoyaltyService.redeem_points(
                request.user, points_amount, currency, order=order
            )
        except ValidationError as e:
            return Response(
                {"detail": e.message},
                status=status.HTTP_400_BAD_REQUEST,
            )

        remaining_balance = LoyaltyService.get_user_balance(request.user)

        response_data = {
            "discount_amount": discount,
            "currency": currency,
            "points_redeemed": points_amount,
            "remaining_balance": remaining_balance,
        }

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            response_data, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["GET"])
    def product_points(self, request, pk=None):
        """GET /api/v1/loyalty/product/<pk>/points - Product points preview."""
        product = get_object_or_404(Product, pk=pk)
        potential_points = LoyaltyService.get_product_potential_points(
            product, request.user
        )

        tier_multiplier_applied = (
            bool(request.user.loyalty_tier)
            and request.user.loyalty_tier.points_multiplier > 1
            and bool(
                Setting.get("LOYALTY_TIER_MULTIPLIER_ENABLED", default=False)
            )
        )

        response_data = {
            "product_id": product.id,
            "potential_points": potential_points,
            "tier_multiplier_applied": tier_multiplier_applied,
        }

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            response_data, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["GET"])
    def tiers(self, request):
        """GET /api/v1/loyalty/tiers - List all loyalty tiers."""
        queryset = LoyaltyTier.objects.for_list().order_by("required_level")

        response_serializer_class = self.get_response_serializer()
        serializer = response_serializer_class(
            queryset, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
