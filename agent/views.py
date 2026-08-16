from __future__ import annotations

from allauth.idp.oidc.contrib.rest_framework.authentication import (
    TokenAuthentication,
)
from allauth.idp.oidc.contrib.rest_framework.permissions import (
    TokenPermission,
)
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from agent.oidc import (
    SCOPE_FAVOURITES_READ,
    SCOPE_LOYALTY_READ,
    SCOPE_ORDERS_READ,
)
from agent.serializers import AgentFavouriteSerializer, AgentProfileSerializer
from loyalty.serializers.loyalty import LoyaltySummarySerializer
from loyalty.services import LoyaltyService
from order.serializers.order import OrderSerializer
from order.services import OrderService
from product.models.favourite import ProductFavourite

# Agents want "my recent items", not a paginated browse — cap the lists.
AGENT_ORDERS_LIMIT = 20
AGENT_FAVOURITES_LIMIT = 30


class AgentAPIView(APIView):
    """Base for agent-facing resources under ``/api/v1/agent/``.

    OIDC bearer tokens ONLY (``allauth.idp`` — never Knox or session
    auth): the agent surface stays isolated from the storefront auth
    stack, so a linked agent can reach exactly the scoped resources
    below and nothing else.
    """

    authentication_classes = [TokenAuthentication]


class AgentMeView(AgentAPIView):
    permission_classes = [TokenPermission.has_scope("profile")]

    @extend_schema(
        operation_id="getAgentProfile",
        responses=AgentProfileSerializer,
        summary="Linked account profile (agent surface)",
        description=(
            "Identity of the shopper who linked their account to the "
            "calling AI agent. Requires an OIDC access token with the "
            "`profile` scope. The agent gateway also uses this endpoint "
            "to validate bearer tokens."
        ),
        tags=["Agent"],
    )
    def get(self, request: Request) -> Response:
        serializer = AgentProfileSerializer(
            {
                "id": request.user.id,
                "email": request.user.email,
                "first_name": request.user.first_name or "",
                "last_name": request.user.last_name or "",
            }
        )
        return Response(serializer.data)


class AgentOrdersView(AgentAPIView):
    permission_classes = [TokenPermission.has_scope(SCOPE_ORDERS_READ)]

    @extend_schema(
        operation_id="listAgentOrders",
        responses=OrderSerializer(many=True),
        summary="Linked account's recent orders (agent surface)",
        description=(
            "The linked shopper's most recent orders (newest first, "
            f"capped at {AGENT_ORDERS_LIMIT}). Requires the "
            "`orders:read` scope."
        ),
        tags=["Agent"],
    )
    def get(self, request: Request) -> Response:
        # TokenPermission guarantees an authenticated user, but the
        # static ``request.user.pk`` type stays ``Any | None``.
        orders = OrderService.get_user_orders(
            request.user.pk  # ty: ignore[invalid-argument-type]
        )[:AGENT_ORDERS_LIMIT]
        serializer = OrderSerializer(
            orders, many=True, context={"request": request}
        )
        return Response(serializer.data)


class AgentFavouritesView(AgentAPIView):
    permission_classes = [TokenPermission.has_scope(SCOPE_FAVOURITES_READ)]

    @extend_schema(
        operation_id="listAgentFavourites",
        responses=AgentFavouriteSerializer(many=True),
        summary="Linked account's favourite products (agent surface)",
        description=(
            "The linked shopper's favourited products (newest first, "
            f"active products only, capped at {AGENT_FAVOURITES_LIMIT}) "
            "— the basis for personalised recommendations. Requires "
            "the `favourites:read` scope."
        ),
        tags=["Agent"],
    )
    def get(self, request: Request) -> Response:
        favourites = (
            ProductFavourite.objects.filter(
                user=request.user, product__active=True
            )
            .select_related("product")
            .prefetch_related("product__translations")
            .order_by("-created_at")[:AGENT_FAVOURITES_LIMIT]
        )
        rows = []
        for favourite in favourites:
            product = favourite.product
            price = product.final_price
            rows.append(
                {
                    "product_id": product.id,
                    "name": product.safe_translation_getter(
                        "name", any_language=True
                    )
                    or "",
                    "final_price": str(price.amount),
                    "currency": str(price.currency),
                    "in_stock": product.stock > 0,
                    "added_at": favourite.created_at,
                }
            )
        serializer = AgentFavouriteSerializer(rows, many=True)
        return Response(serializer.data)


class AgentLoyaltyView(AgentAPIView):
    permission_classes = [TokenPermission.has_scope(SCOPE_LOYALTY_READ)]

    @extend_schema(
        operation_id="getAgentLoyaltySummary",
        responses=LoyaltySummarySerializer,
        summary="Linked account's loyalty summary (agent surface)",
        description=(
            "Points balance, XP, level and tier of the linked shopper. "
            "Requires the `loyalty:read` scope."
        ),
        tags=["Agent"],
    )
    def get(self, request: Request) -> Response:
        serializer = LoyaltySummarySerializer(
            LoyaltyService.get_user_summary(request.user),
            context={"request": request},
        )
        return Response(serializer.data)
