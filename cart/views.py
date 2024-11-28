from __future__ import annotations

from typing import override

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import throttle_classes
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from cart.models import Cart
from cart.models import CartItem
from cart.serializers import CartItemCreateSerializer
from cart.serializers import CartItemSerializer
from cart.serializers import CartSerializer
from cart.service import CartService
from core.api.throttling import BurstRateThrottle
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin


class CartViewSet(BaseModelViewSet):
    queryset = Cart.objects.all()
    serializer_class = CartSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["user"]
    ordering_fields = ["user", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["user"]

    @override
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = CartService(request=self.request).cart
        return context

    @override
    def retrieve(self, request, *args, **kwargs) -> Response:
        cart = CartService(request=request).get_or_create_cart(request)
        serializer = self.get_serializer(cart)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @throttle_classes([BurstRateThrottle])
    @override
    def update(self, request, *args, **kwargs) -> Response:
        instance = CartService(request=request).get_or_create_cart(request)
        serializer = self.get_serializer(instance, data=request.data, partial=kwargs.pop("partial", False))
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    @throttle_classes([BurstRateThrottle])
    @override
    def partial_update(self, request, *args, **kwargs) -> Response:
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @override
    def destroy(self, request, *args, **kwargs) -> Response:
        cart_service = CartService(request=request)
        if cart_service.cart:
            cart_service.cart.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_404_NOT_FOUND)


class CartItemViewSet(MultiSerializerMixin, BaseModelViewSet):
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["cart"]
    ordering = ["-created_at"]
    ordering_fields = ["cart", "created_at"]
    search_fields = ["cart"]

    serializers = {
        "default": CartItemSerializer,
        "list": CartItemSerializer,
        "create": CartItemCreateSerializer,
        "retrieve": CartItemSerializer,
        "update": CartItemSerializer,
        "partial_update": CartItemSerializer,
        "destroy": CartItemSerializer,
    }

    @override
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CartItem.objects.none()

        cart_id = self.request.session.get("cart_id")
        if self.request.user.is_anonymous:
            return CartItem.objects.filter(cart__id=cart_id)
        return CartItem.objects.filter(cart__user=self.request.user)

    @override
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = CartService(request=self.request).get_or_create_cart(self.request)
        return context

    @throttle_classes([BurstRateThrottle])
    @override
    def create(self, request, *args, **kwargs):
        service = CartService(request=request)
        serializer = self.get_serializer(data=request.data)
        serializer.context["cart"] = service.get_or_create_cart(request)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
            headers=self.get_success_headers(serializer.data),
        )
