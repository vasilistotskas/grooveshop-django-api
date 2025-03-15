from __future__ import annotations

from typing import override

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import throttle_classes
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from cart.models import Cart, CartItem
from cart.serializers import (
    CartItemCreateSerializer,
    CartItemSerializer,
    CartSerializer,
)
from cart.service import CartService
from core.api.throttling import BurstRateThrottle
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin


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

    @override
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

    @throttle_classes([BurstRateThrottle])
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

    @throttle_classes([BurstRateThrottle])
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if cart:
            cart.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_404_NOT_FOUND)


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

    @override
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CartItem.objects.none()

        if self.cart_service.cart:
            if self.request.user.is_anonymous:
                cart_id = self.request.session.get("cart_id")
                return CartItem.objects.filter(cart__id=cart_id)
            return CartItem.objects.filter(cart__user=self.request.user)
        return CartItem.objects.none()

    @override
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = self.cart_service.cart
        return context

    @throttle_classes([BurstRateThrottle])
    @override
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    @throttle_classes([BurstRateThrottle])
    @override
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    @throttle_classes([BurstRateThrottle])
    @override
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @override
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)
