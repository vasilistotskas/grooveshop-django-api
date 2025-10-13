from __future__ import annotations

from django.http import Http404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
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
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
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

req_serializers: RequestSerializersConfig = {
    "create": CartItemCreateSerializer,
    "update": CartItemUpdateSerializer,
    "partial_update": CartItemUpdateSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": CartItemDetailSerializer,
    "list": CartItemSerializer,
    "retrieve": CartItemDetailSerializer,
    "update": CartItemDetailSerializer,
    "partial_update": CartItemDetailSerializer,
}

cart_item_schema_config = create_schema_view_config(
    model_class=CartItem,
    request_serializers=req_serializers,
    response_serializers=res_serializers,
    error_serializer=ErrorResponseSerializer,
    display_config={
        "tag": "Cart Items",
        "display_name": _("cart item"),
        "display_name_plural": _("cart items"),
    },
)

cart_item_schema_config.update(
    {
        "list": extend_schema(
            operation_id="listCartItem",
            summary=_("List cart items"),
            description=_(
                "Retrieve a list of cart items with filtering and search capabilities. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart Items"],
            parameters=GUEST_CART_HEADERS,
            responses={
                200: CartItemSerializer(many=True),
                400: ErrorResponseSerializer,
                401: ErrorResponseSerializer,
                403: ErrorResponseSerializer,
                404: ErrorResponseSerializer,
            },
        ),
        "create": extend_schema(
            operation_id="createCartItem",
            summary=_("Create a cart item"),
            description=_(
                "Create a new cart item. Requires authentication. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart Items"],
            parameters=GUEST_CART_HEADERS,
            request=CartItemCreateSerializer,
            responses={
                201: CartItemDetailSerializer,
                400: ErrorResponseSerializer,
                401: ErrorResponseSerializer,
                403: ErrorResponseSerializer,
            },
        ),
        "retrieve": extend_schema(
            operation_id="retrieveCartItem",
            summary=_("Retrieve a cart item"),
            description=_(
                "Get detailed information about a specific cart item. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart Items"],
            parameters=GUEST_CART_HEADERS,
            responses={
                200: CartItemDetailSerializer,
                401: ErrorResponseSerializer,
                403: ErrorResponseSerializer,
                404: ErrorResponseSerializer,
            },
        ),
        "update": extend_schema(
            operation_id="updateCartItem",
            summary=_("Update a cart item"),
            description=_(
                "Update cart item information. Requires authentication. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart Items"],
            parameters=GUEST_CART_HEADERS,
            request=CartItemUpdateSerializer,
            responses={
                200: CartItemDetailSerializer,
                400: ErrorResponseSerializer,
                401: ErrorResponseSerializer,
                403: ErrorResponseSerializer,
                404: ErrorResponseSerializer,
            },
        ),
        "partial_update": extend_schema(
            operation_id="partialUpdateCartItem",
            summary=_("Partially update a cart item"),
            description=_(
                "Partially update cart item information. Requires authentication. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart Items"],
            parameters=GUEST_CART_HEADERS,
            request=CartItemUpdateSerializer,
            responses={
                200: CartItemDetailSerializer,
                400: ErrorResponseSerializer,
                401: ErrorResponseSerializer,
                403: ErrorResponseSerializer,
                404: ErrorResponseSerializer,
            },
        ),
        "destroy": extend_schema(
            operation_id="destroyCartItem",
            summary=_("Delete a cart item"),
            description=_(
                "Delete a cart item. Requires authentication. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart Items"],
            parameters=GUEST_CART_HEADERS,
            responses={
                204: None,
                401: ErrorResponseSerializer,
                403: ErrorResponseSerializer,
                404: ErrorResponseSerializer,
            },
        ),
    }
)


@extend_schema_view(**cart_item_schema_config)
class CartItemViewSet(BaseModelViewSet):
    queryset = CartItem.objects.all()
    response_serializers = res_serializers
    request_serializers = req_serializers
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
