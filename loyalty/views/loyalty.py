from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.api.query_params import parse_int_csv
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.store_settings import store_setting
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
from tenant.permissions import IsLoyaltyEnabled, IsLoyaltyRuntimeEnabled

logger = logging.getLogger(__name__)

# A cart's distinct products, at most: one request previews them all.
MAX_POINTS_PREVIEW_PRODUCTS = 100

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
    "products_points": ActionConfig(
        response=ProductPointsSerializer,
        many=True,
        paginated=False,
        operation_id="getProductsLoyaltyPoints",
        summary=_("Get loyalty points preview for several products"),
        description=_(
            "The points the signed-in customer would earn per product, for "
            "every listed product that exists. A product that does not "
            "exist is left out."
        ),
        tags=["Loyalty"],
        parameters=[
            OpenApiParameter(
                name="productIds",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                description=format_lazy(
                    _(
                        "Comma-separated IDs of the products to preview "
                        "points for, from 1 to {max}."
                    ),
                    max=MAX_POINTS_PREVIEW_PRODUCTS,
                ),
            ),
        ],
    ),
    "tiers": ActionConfig(
        response=LoyaltyTierSerializer,
        many=True,
        paginated=False,
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
    redeeming points, and previewing product points. Everything that
    reads or moves a CUSTOMER's points requires authentication; the tier
    ladder does not — see ``get_permissions``.
    """

    queryset = PointsTransaction.objects.none()
    # The plan flag and the merchant's runtime switch fire first, each
    # raising 404, so a store with the programme off serves no points,
    # history or redemption. IsAuthenticated covers the rest: the
    # queryset filters by request.user and lives in the tenant schema,
    # so an authenticated caller here is by construction a customer of
    # THIS store (see tenant/membership.py).
    permission_classes = [
        IsLoyaltyEnabled,
        IsLoyaltyRuntimeEnabled,
        IsAuthenticated,
    ]
    serializers_config = serializers_config

    def get_permissions(self):
        """The tier ladder is marketing copy, not account data.

        ``/loyalty-program`` is a public page whose whole content is the
        tiers — their names, the level each starts at, what each
        multiplies — and it is identical for every visitor of a store.
        Behind ``IsAuthenticated`` it could only be server-rendered for
        a signed-in shopper, so the page that exists to SELL the
        programme showed nothing to the people it is aimed at.

        Both gates stay: the plan flag and the merchant's runtime
        switch, each 404ing, so a store with the programme off does not
        keep an indexable page advertising it.
        """
        if self.action == "tiers":
            return [
                AllowAny(),
                IsLoyaltyEnabled(),
                IsLoyaltyRuntimeEnabled(),
            ]
        return super().get_permissions()

    @action(detail=False, methods=["GET"])
    def summary(self, request):
        """GET /api/v1/loyalty/summary - User's loyalty summary."""
        data = LoyaltyService.get_user_summary(request.user)

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
        order_id = serializer.validated_data["order_id"]

        from order.enum.status import OrderStatus
        from order.models.order import Order

        try:
            order = Order.objects.get(id=order_id, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {
                    "detail": _("Order not found or does not belong to you."),
                    "code": "order_not_found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # A redemption only reduces what the customer pays while the order is
        # still pending; redeeming against an already-settled order would just
        # burn points for nothing.
        if order.status != OrderStatus.PENDING:
            return Response(
                {
                    "detail": _(
                        "Points can only be redeemed on a pending order."
                    ),
                    "code": "order_not_pending",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # One redemption per order — otherwise repeated calls drain the
        # balance while only the last discount is recorded on the order.
        from loyalty.enum import TransactionType

        if PointsTransaction.objects.filter(
            reference_order=order,
            transaction_type=TransactionType.REDEEM,
        ).exists():
            return Response(
                {
                    "detail": _(
                        "Points have already been redeemed for this order."
                    ),
                    "code": "points_already_redeemed",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cap discount to the products total (excluding shipping/fees)
        max_discount = order.total_price_items.amount

        try:
            discount = LoyaltyService.redeem_points(
                user=request.user,
                points_amount=points_amount,
                currency=currency,
                max_discount=max_discount,
                order=order,
            )
        except ValidationError as e:
            return Response(
                {"detail": e.message, "code": "redemption_invalid"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Persist the discount on the order — every charge site reads
        # calculate_order_total_amount(), which only subtracts what is
        # stored on the row. The checkout path always did this; this
        # standalone endpoint used to burn the points while leaving
        # the order total (and therefore the charge) untouched.
        from django.db import transaction
        from djmoney.money import Money

        from order.discounts import (
            add_line_discounts,
            spread_by_remaining_value,
        )

        with transaction.atomic():
            order.loyalty_discount = Money(discount, currency)
            order.amount_due = order.calculate_order_total_amount()
            order.save(
                update_fields=[
                    "loyalty_discount",
                    "loyalty_discount_currency",
                    "amount_due",
                    "amount_due_currency",
                ]
            )
            # The tax documents read where the discount landed per line
            # (order.discounts): the redemption is order-wide, so it is
            # spread over the value the lines still carry.
            items = list(order.items.all())
            add_line_discounts(
                items, spread_by_remaining_value(items, discount)
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

    def _points_preview(self, products) -> list[dict]:
        """Each product's points for the caller, at their current tier."""
        user = self.request.user
        tier_multiplier_applied = (
            bool(user.loyalty_tier)
            and user.loyalty_tier.points_multiplier > 1
            and bool(store_setting("LOYALTY_TIER_MULTIPLIER_ENABLED"))
        )
        return [
            {
                "product_id": product.id,
                "potential_points": LoyaltyService.get_product_potential_points(
                    product, user
                ),
                "tier_multiplier_applied": tier_multiplier_applied,
            }
            for product in products
        ]

    @action(detail=True, methods=["GET"])
    def product_points(self, request, pk=None):
        """GET /api/v1/loyalty/product/<pk>/points - Product points preview."""
        product = get_object_or_404(Product, pk=pk)
        (response_data,) = self._points_preview([product])

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            response_data, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["GET"], pagination_class=None)
    def products_points(self, request):
        """GET /api/v1/loyalty/product/points?productIds=1,2 - Points
        preview per product.

        One request for a cart's products instead of one per product.
        """
        product_ids = parse_int_csv(
            request.query_params.get("product_ids"), "productIds"
        )
        if not (
            1 <= len(product_ids) <= MAX_POINTS_PREVIEW_PRODUCTS
            and min(product_ids) >= 1
        ):
            raise DRFValidationError(
                {
                    "productIds": _(
                        "Give from 1 to %(max)d product IDs, each a positive "
                        "integer."
                    )
                    % {"max": MAX_POINTS_PREVIEW_PRODUCTS}
                },
                code="invalid_product_ids",
            )

        products = (
            Product.objects.filter(pk__in=product_ids)
            # ``final_price`` and ``vat_percent`` read the VAT row.
            .select_related("vat")
            .order_by("id")
        )

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            self._points_preview(products),
            many=True,
            context=self.get_serializer_context(),
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
