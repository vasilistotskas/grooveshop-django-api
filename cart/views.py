from __future__ import annotations

from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from cart.models import CartItem
from cart.serializers import CartItemCreateSerializer
from cart.serializers import CartItemSerializer
from cart.serializers import CartSerializer
from cart.service import CartService


class CartViewSet(ModelViewSet):
    serializer_class = CartSerializer

    def get_queryset(self):
        service = CartService(self.request)
        return service.cart

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = self.get_queryset()
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

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CartItem.objects.none()
        service = CartService(self.request)
        return service.cart_items

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = self.get_cart()
        return context

    def get_cart(self):
        service = CartService(self.request)
        return service.cart

    def list(self, request, *args, **kwargs):
        service = CartService(self.request)
        queryset = service.cart_items
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        serializer = CartItemCreateSerializer(data=request.data)
        serializer.context["cart"] = self.get_cart()
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        service = CartService(self.request)
        queryset = service.cart_items
        cart_item = get_object_or_404(queryset, pk=pk)
        serializer = self.get_serializer(cart_item)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        service = CartService(self.request)
        queryset = service.cart_items
        cart_item = get_object_or_404(queryset, pk=pk)
        serializer = self.get_serializer(cart_item, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        service = CartService(self.request)
        queryset = service.cart_items
        cart_item = get_object_or_404(queryset, pk=pk)
        serializer = self.get_serializer(cart_item, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        service = CartService(self.request)
        queryset = service.cart_items
        cart_item = get_object_or_404(queryset, pk=pk)
        cart_item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
