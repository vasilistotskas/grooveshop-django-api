from __future__ import annotations

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
    filterset_fields = [
        "user",
    ]
    ordering_fields = ["user", "-created_at"]
    ordering = ["-created_at"]
    search_fields = [
        "user",
    ]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        cart_service = CartService(request=self.request)
        context["cart"] = cart_service.cart
        return context

    def retrieve(self, request, *args, **kwargs) -> Response:
        service = CartService(request=request)
        serializer = self.get_serializer(service.get_or_create_cart(request))
        return Response(serializer.data, status=status.HTTP_200_OK)

    @throttle_classes([BurstRateThrottle])
    def update(self, request, *args, **kwargs) -> Response:
        partial = kwargs.pop("partial", False)
        service = CartService(request=request)
        instance = service.get_or_create_cart(request)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    @throttle_classes([BurstRateThrottle])
    def partial_update(self, request, *args, **kwargs) -> Response:
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs) -> Response:
        service = CartService(request=request)
        if service.cart:
            service.cart.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_404_NOT_FOUND)


class CartItemViewSet(MultiSerializerMixin, BaseModelViewSet):
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = [
        "cart",
    ]
    ordering = ["-created_at"]
    ordering_fields = ["cart", "-created_at"]
    search_fields = [
        "cart",
    ]

    serializers = {
        "default": CartItemSerializer,
        "list": CartItemSerializer,
        "create": CartItemCreateSerializer,
        "retrieve": CartItemSerializer,
        "update": CartItemSerializer,
        "partial_update": CartItemSerializer,
        "destroy": CartItemSerializer,
    }

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CartItem.objects.none()

        if self.request.user.is_anonymous:
            return CartItem.objects.filter(cart__id=self.request.session.get("cart_id"))
        return CartItem.objects.filter(cart__user=self.request.user)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        service = CartService(request=self.request)
        context["cart"] = service.get_or_create_cart(self.request)
        return context

    @throttle_classes([BurstRateThrottle])
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        service = CartService(request=request)
        serializer.context["cart"] = service.get_or_create_cart(request)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )
