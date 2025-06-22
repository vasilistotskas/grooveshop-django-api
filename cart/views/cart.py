from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.filters import SearchFilter
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
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
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
    OpenApiParameter(
        name="X-Session-Key",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.HEADER,
        description="Session key for guest users. Used to validate cart ownership for anonymous sessions.",
        required=False,
    ),
]

cart_schema_config = create_schema_view_config(
    model_class=Cart,
    serializers={
        "list_serializer": CartSerializer,
        "detail_serializer": CartDetailSerializer,
        "write_serializer": CartWriteSerializer,
    },
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
                "Get a cart. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
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
                "Get a cart. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
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
                "Update a cart. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
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
                "Update a cart. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
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
                "Delete a cart. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
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
class CartViewSet(MultiSerializerMixin, BaseModelViewSet):
    cart_service: CartService
    queryset = Cart.objects.all()
    serializers = {
        "default": CartDetailSerializer,
        "list": CartSerializer,
        "retrieve": CartDetailSerializer,
        "update": CartWriteSerializer,
        "partial_update": CartWriteSerializer,
    }
    response_serializers = {
        "update": CartDetailSerializer,
        "partial_update": CartDetailSerializer,
    }
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = CartFilter
    ordering_fields = [
        "id",
        "user",
        "created_at",
        "updated_at",
        "last_activity",
    ]
    ordering = ["-created_at"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.cart_service = CartService(request=request)

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
        serializer = self.get_serializer(cart)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(
            cart, data=request.data, partial=kwargs.pop("partial", False)
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        response_serializer = CartDetailSerializer(
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
