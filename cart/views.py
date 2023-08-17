from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.filters import SearchFilter
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from cart.models import CartItem
from cart.paginators import CartItemPagination
from cart.paginators import CartPagination
from cart.serializers import CartItemCreateSerializer
from cart.serializers import CartItemSerializer
from cart.serializers import CartSerializer
from cart.service import CartService
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter


class CartViewSet(ModelViewSet):
    serializer_class = CartSerializer
    pagination_class = CartPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = [
        "user",
    ]
    ordering_fields = ["user", "-created_at"]
    ordering = ["-created_at"]
    search_fields = [
        "user",
    ]

    def get_queryset(self):
        service = CartService(self.request)
        return service.cart

    def get_serializer_context(self):
        context = super().get_serializer_context()
        cart_service = CartService(self.request)
        context["cart"] = cart_service.cart
        return context

    def retrieve(self, request, *args, **kwargs) -> Response:
        service = CartService(self.request)
        serializer = self.get_serializer(service.cart)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs) -> Response:
        service = CartService(self.request)
        serializer = self.get_serializer(service.cart, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, *args, **kwargs) -> Response:
        service = CartService(self.request)
        serializer = self.get_serializer(service.cart, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs) -> Response:
        service = CartService(self.request)
        service.cart.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CartItemViewSet(ModelViewSet):
    serializer_class = CartItemSerializer
    pagination_class = CartItemPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = [
        "cart",
    ]
    ordering = ["-created_at"]
    ordering_fields = ["cart", "-created_at"]
    search_fields = [
        "cart",
    ]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CartItem.objects.none()
        return CartItem.objects.filter(cart__user=self.request.user)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = CartService(self.request).cart
        return context

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        serializer = CartItemCreateSerializer(data=request.data)
        serializer.context["cart"] = CartService(self.request).cart
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        cart_item = self.get_object()
        serializer = self.get_serializer(cart_item)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        cart_item = self.get_object()
        serializer = self.get_serializer(cart_item, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        cart_item = self.get_object()
        serializer = self.get_serializer(cart_item, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        cart_item = self.get_object()
        cart_item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
