from __future__ import annotations

from django.http import Http404
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

from cart.models import Cart, CartItem
from cart.serializers import (
    CartItemCreateSerializer,
    CartItemSerializer,
    CartSerializer,
)
from cart.services import CartService
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin

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


@extend_schema_view(
    list=extend_schema(
        summary=_("Get cart"),
        description=_(
            "Get a cart. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
        responses={
            200: CartSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Get cart"),
        description=_(
            "Get a cart. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
        responses={
            200: CartSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update cart"),
        description=_(
            "Update a cart. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
        responses={
            200: CartSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Update cart"),
        description=_(
            "Update a cart. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
        responses={
            200: CartSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete cart"),
        description=_(
            "Delete a cart. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
        responses={
            200: CartSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
class CartViewSet(BaseModelViewSet):
    queryset = Cart.objects.all()
    serializer_class = CartSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["user"]
    ordering_fields = ["user", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["user"]
    cart_service: CartService

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
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if cart:
            cart.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_404_NOT_FOUND)


@extend_schema_view(
    list=extend_schema(
        summary=_("Get cart items"),
        description=_(
            "Get all cart items. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
        responses={
            200: CartItemSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create cart item"),
        description=_(
            "Create a new cart item. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
        request=CartItemCreateSerializer,
        responses={
            201: CartItemSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Get cart item"),
        description=_(
            "Get a specific cart item. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
        responses={
            200: CartItemSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update cart item"),
        description=_(
            "Update a specific cart item. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
        request=CartItemCreateSerializer,
        responses={
            200: CartItemSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partial update cart item"),
        description=_(
            "Partial update a specific cart item. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
        request=CartItemCreateSerializer,
        responses={
            200: CartItemSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete cart item"),
        description=_(
            "Delete a specific cart item. For guest users, include X-Cart-Id and X-Session-Key headers to maintain cart session."
        ),
        tags=["Cart Items"],
        parameters=GUEST_CART_HEADERS,
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
class CartItemViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = CartItem.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["cart"]
    ordering = ["-created_at"]
    ordering_fields = ["cart", "created_at"]
    search_fields = ["cart"]
    cart_service: CartService

    serializers = {
        "default": CartItemSerializer,
        "list": CartItemSerializer,
        "create": CartItemCreateSerializer,
        "retrieve": CartItemSerializer,
        "update": CartItemSerializer,
        "partial_update": CartItemSerializer,
        "destroy": CartItemSerializer,
    }

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
        try:
            obj = super().get_object()

            if obj.cart != self.cart_service.cart:
                self.permission_denied(
                    self.request,
                    message=_(
                        "You do not have permission to access this cart item."
                    ),
                )

            return obj
        except CartItem.DoesNotExist:
            self.permission_denied(
                self.request,
                message=_(
                    "You do not have permission to access this cart item."
                ),
            )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = self.cart_service.cart
        return context

    def create(self, request, *args, **kwargs):
        if not self.cart_service.cart:
            self.cart_service.get_or_create_cart()

        data = request.data.copy()
        data["cart"] = self.cart_service.cart.id

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)

        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)

        try:
            instance = self.get_object()

            serializer = self.get_serializer(
                instance, data=request.data, partial=partial
            )
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(serializer.data)
        except Http404:
            return Response(
                {
                    "detail": _(
                        "You do not have permission to update this cart item."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

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
