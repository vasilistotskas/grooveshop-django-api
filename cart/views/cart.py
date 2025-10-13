from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.permissions import IsAdminUser

from rest_framework.response import Response

from cart.filters.cart import CartFilter
from cart.models import Cart
from cart.serializers.cart import (
    CartDetailSerializer,
    CartSerializer,
    CartWriteSerializer,
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
    "update": CartWriteSerializer,
    "partial_update": CartWriteSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "list": CartSerializer,
    "retrieve": CartDetailSerializer,
    "update": CartDetailSerializer,
    "partial_update": CartDetailSerializer,
}

cart_schema_config = create_schema_view_config(
    model_class=Cart,
    request_serializers=req_serializers,
    response_serializers=res_serializers,
    error_serializer=ErrorResponseSerializer,
    display_config={
        "tag": "Cart",
        "display_name": _("cart"),
        "display_name_plural": _("carts"),
    },
)

cart_schema_config.update(
    {
        "list": extend_schema(
            operation_id="listCart",
            summary=_("Get cart"),
            description=_(
                "Get a cart. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart"],
            parameters=GUEST_CART_HEADERS,
            responses={
                200: CartSerializer(many=True),
                404: ErrorResponseSerializer,
            },
        ),
        "retrieve": extend_schema(
            operation_id="retrieveCart",
            summary=_("Get cart"),
            description=_(
                "Get a cart. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart"],
            parameters=GUEST_CART_HEADERS,
            responses={
                200: CartDetailSerializer,
                404: ErrorResponseSerializer,
            },
        ),
        "update": extend_schema(
            operation_id="updateCart",
            summary=_("Update cart"),
            description=_(
                "Update a cart. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart"],
            parameters=GUEST_CART_HEADERS,
            request=CartWriteSerializer,
            responses={
                200: CartDetailSerializer,
                404: ErrorResponseSerializer,
            },
        ),
        "partial_update": extend_schema(
            operation_id="partialUpdateCart",
            summary=_("Update cart"),
            description=_(
                "Update a cart. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart"],
            parameters=GUEST_CART_HEADERS,
            request=CartWriteSerializer,
            responses={
                200: CartDetailSerializer,
                404: ErrorResponseSerializer,
            },
        ),
        "destroy": extend_schema(
            operation_id="destroyCart",
            summary=_("Delete cart"),
            description=_(
                "Delete a cart. For guest users, include X-Cart-Id header to maintain cart session."
            ),
            tags=["Cart"],
            parameters=GUEST_CART_HEADERS,
            responses={
                204: None,
                401: ErrorResponseSerializer,
                404: ErrorResponseSerializer,
            },
        ),
        "create": extend_schema(
            operation_id="createCart",
            summary=_("Create cart"),
            description=_("Cart creation is not allowed via API."),
            tags=["Cart"],
            responses={
                405: ErrorResponseSerializer,
            },
        ),
    }
)


@extend_schema_view(**cart_schema_config)
class CartViewSet(BaseModelViewSet):
    cart_service: CartService
    queryset = Cart.objects.all()
    response_serializers = res_serializers
    request_serializers = req_serializers
    filterset_class = CartFilter
    ordering_fields = [
        "id",
        "user",
        "created_at",
        "updated_at",
        "last_activity",
    ]
    ordering = ["-last_activity", "-created_at"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.cart_service = CartService(request=request)

    def get_permissions(self):
        if self.action in [
            "list",
        ]:
            self.permission_classes = [IsAdminUser]
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user

        if user.is_staff:
            return Cart.objects.all()
        elif user.is_authenticated:
            return Cart.objects.filter(user=user)
        elif self.cart_service.cart:
            return Cart.objects.filter(id=self.cart_service.cart.id)

        return Cart.objects.none()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = self.cart_service.cart
        return context

    def create(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def retrieve(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(status=status.HTTP_404_NOT_FOUND)
        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(cart)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(status=status.HTTP_404_NOT_FOUND)
        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(
            cart, data=request.data, partial=kwargs.pop("partial", False)
        )
        request_serializer.is_valid(raise_exception=True)
        self.perform_update(request_serializer)

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            cart, context=self.get_serializer_context()
        )
        return Response(response_serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if cart:
            cart.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_404_NOT_FOUND)
