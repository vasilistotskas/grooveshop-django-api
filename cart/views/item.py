from __future__ import annotations

from django.http import Http404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.permissions import AllowAny

from rest_framework.response import Response

from cart.filters.item import CartItemFilter
from cart.models import CartItem
from cart.serializers.item import (
    CartItemDetailSerializer,
    CartItemSerializer,
    CartItemCreateSerializer,
    CartItemUpdateSerializer,
)
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from cart.services import CartService
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import (
    CartMutationAnonThrottle,
    CartMutationThrottle,
)
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from tenant.membership import is_store_staff

GUEST_CART_HEADERS = [
    OpenApiParameter(
        name="X-Cart-Id",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.HEADER,
        description="Guest cart UUID. Used to identify and maintain guest cart sessions.",
        required=False,
    ),
]

serializers_config: SerializersConfig = {
    "list": ActionConfig(
        response=CartItemSerializer,
        many=True,
        operation_id="listCartItem",
        summary=_("List cart items"),
        description=_(
            "Retrieve a list of cart items with filtering and search capabilities. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
    ),
    "create": ActionConfig(
        request=CartItemCreateSerializer,
        response=CartItemDetailSerializer,
        operation_id="createCartItem",
        summary=_("Create a cart item"),
        description=_(
            "Create a new cart item. Requires authentication. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
    ),
    "retrieve": ActionConfig(
        response=CartItemDetailSerializer,
        operation_id="retrieveCartItem",
        summary=_("Retrieve a cart item"),
        description=_(
            "Get detailed information about a specific cart item. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
    ),
    "update": ActionConfig(
        request=CartItemUpdateSerializer,
        response=CartItemDetailSerializer,
        operation_id="updateCartItem",
        summary=_("Update a cart item"),
        description=_(
            "Update cart item information. Requires authentication. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
    ),
    "partial_update": ActionConfig(
        request=CartItemUpdateSerializer,
        response=CartItemDetailSerializer,
        operation_id="partialUpdateCartItem",
        summary=_("Partially update a cart item"),
        description=_(
            "Partially update cart item information. Requires authentication. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
    ),
    "destroy": ActionConfig(
        operation_id="destroyCartItem",
        summary=_("Delete a cart item"),
        description=_(
            "Delete a cart item. Requires authentication. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=CartItem,
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
        display_config={
            "tag": "Cart Items",
            "display_name": _("cart item"),
            "display_name_plural": _("cart items"),
        },
    )
)
class CartItemViewSet(BaseModelViewSet):
    queryset = CartItem.objects.all()
    serializers_config = serializers_config
    filterset_class = CartItemFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "quantity",
        "cart__id",
        "cart__last_activity",
        "product__id",
    ]
    ordering = ["-cart__last_activity", "-created_at"]
    search_fields = [
        "product__translations__name",
        "cart__user__email",
    ]
    cart_service: CartService

    _MUTATION_ACTIONS = frozenset(
        {"create", "update", "partial_update", "destroy"}
    )

    def get_permissions(self):
        # All cart item operations support guest users via X-Cart-Id header.
        # get_queryset() enforces ownership so non-admin users only see their
        # own cart's items.
        self.permission_classes = [AllowAny]
        return super().get_permissions()

    def get_throttles(self):
        if self.action in self._MUTATION_ACTIONS:
            # Layer per-minute burst limits ON TOP of the global daily caps.
            return [
                CartMutationThrottle(),
                CartMutationAnonThrottle(),
                AnonRateThrottle(),
                UserRateThrottle(),
            ]
        return super().get_throttles()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.cart_service = CartService(request=request)

    def get_queryset(self):
        user = self.request.user

        # Route through the optimizers so the nested ProductSerializer in the
        # item list doesn't N+1 per line (G0088): for_list() prefetches the
        # product + translations, for_detail() adds product images.
        base = (
            CartItem.objects.for_list()
            if self.action == "list"
            else CartItem.objects.for_detail()
        )

        if is_store_staff(user):
            return base

        if not self.cart_service.cart:
            return CartItem.objects.none()

        return base.filter(cart=self.cart_service.cart)

    def _repoint_to_bound_cart(self, item):
        """Point an item's ``cart`` at the service's BOUND instance.

        The optimizers' ``select_related("cart")`` and get_object's
        lazy load materialize fresh cart objects, which silently price
        the line at retail for wholesale buyers. Only items of the
        request's own cart are re-pointed — staff listings of other
        carts stay untouched. (The base viewset instantiates response
        serializers directly, so this can't hook get_serializer.)
        """
        bound_cart = self.cart_service.cart
        if (
            bound_cart is not None
            and getattr(bound_cart, "_b2b_pricing", None) is not None
            and item.cart_id == bound_cart.pk
        ):
            item.cart = bound_cart
        return item

    def get_object(self):
        pk = self.kwargs.get("pk")

        try:
            obj = CartItem.objects.get(pk=pk)

            if obj.cart != self.cart_service.cart:
                self.permission_denied(
                    self.request,
                    message=str(
                        _(
                            "You do not have permission to access this cart item."
                        )
                    ),
                )

            return self._repoint_to_bound_cart(obj)
        except CartItem.DoesNotExist:
            raise Http404(str(_("No CartItem matches the given query.")))

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = self.cart_service.cart
        return context

    def list(self, request, *args, **kwargs):
        bound_cart = self.cart_service.cart
        if (
            bound_cart is not None
            and getattr(bound_cart, "_b2b_pricing", None) is not None
        ):
            items = [
                self._repoint_to_bound_cart(item)
                for item in self.filter_queryset(self.get_queryset())
            ]
            return self.paginate_and_serialize(
                items, request, serializer_class=self.get_response_serializer()
            )
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        if not self.cart_service.cart:
            self.cart_service.cart = self.cart_service.get_or_create_cart()
            self.cart_service.cart_items = (
                self.cart_service.cart.get_items()
                if self.cart_service.cart
                else []
            )

        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            self.perform_destroy(instance)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Http404:
            return Response(
                {
                    "detail": _(
                        "You do not have permission to delete this cart item."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )
