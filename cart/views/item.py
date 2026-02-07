from __future__ import annotations

from django.http import Http404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema_view,
)
from rest_framework import status

from rest_framework.response import Response

from cart.filters.item import CartItemFilter
from cart.models import CartItem
from cart.serializers.item import (
    CartItemDetailSerializer,
    CartItemSerializer,
    CartItemCreateSerializer,
    CartItemUpdateSerializer,
)
from cart.services import CartService
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)

GUEST_CART_HEADERS = [
    OpenApiParameter(
        name="X-Cart-Id",
        type=OpenApiTypes.INT,
        location=OpenApiParameter.HEADER,
        description="Cart ID for guest users. Used to identify and maintain guest cart sessions.",
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

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.cart_service = CartService(request=request)

    def get_queryset(self):
        user = self.request.user

        if user.is_staff:
            return CartItem.objects.all()

        if not self.cart_service.cart:
            return CartItem.objects.none()

        return CartItem.objects.filter(cart=self.cart_service.cart)

    def get_object(self):
        pk = self.kwargs.get("pk")

        try:
            obj = CartItem.objects.get(pk=pk)

            if obj.cart != self.cart_service.cart:
                self.permission_denied(
                    self.request,
                    message=_(
                        "You do not have permission to access this cart item."
                    ),
                )

            return obj
        except CartItem.DoesNotExist:
            raise Http404(_("No CartItem matches the given query."))

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = self.cart_service.cart
        return context

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
