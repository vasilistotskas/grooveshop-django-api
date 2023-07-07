from django.contrib.auth import get_user_model
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from order.enum.pay_way_enum import PayWayEnum
from order.models import Order
from order.paginators import OrderListPagination
from order.serializers import OrderSerializer
from pay_way.models import PayWay

User = get_user_model()


class Checkout(APIView):
    serializer_class = OrderSerializer
    queryset = Order.objects.all()
    pay_way = ""

    @staticmethod
    def decrease_product_stock(product) -> None:
        for item in product:
            quantity = item.get("quantity")
            product = item.get("product")
            product.stock -= quantity
            product.save(update_fields=["stock"])

    @staticmethod
    def calculate_order_total_amount(items) -> float:
        paid_amount = sum(
            item.get("quantity") * item.get("product").price for item in items
        )
        pay_way_cost = 0
        pay_way = PayWay.objects.get(name=PayWayEnum.CREDIT_CARD)
        if pay_way.free_for_order_amount > paid_amount:
            pay_way_cost = pay_way.cost
        paid_amount += pay_way_cost
        return paid_amount

    def create_order(self, request, paid_amount, serializer, items, pay_way_name):
        self.decrease_product_stock(items)

        if request.data.get("user_id"):
            user_id = request.data.get("user_id")
            user = User.objects.get(id=user_id)
            serializer.save(user=user, paid_amount=paid_amount)
        else:
            serializer.save(user=None, paid_amount=paid_amount)

    def post(self, request, format=None):
        serializer = OrderSerializer(data=request.data, context={"request": request})
        if serializer.is_valid(raise_exception=True):
            items = serializer.validated_data["items"]
            paid_amount = self.calculate_order_total_amount(items=items)
            pay_way_name = serializer.validated_data["pay_way"]

            self.pay_way = PayWay.objects.get(name=pay_way_name)

            self.create_order(
                request=request,
                paid_amount=paid_amount,
                serializer=serializer,
                items=items,
                pay_way_name=pay_way_name,
            )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class OrderViewSet(ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    pagination_class = OrderListPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    ordering_fields = ["created_at", "status"]
    filterset_fields = ["user_id", "status"]
    ordering = ["-created_at"]
    search_fields = ["email", "user_id"]

    def list(self, request, *args, **kwargs) -> Response:
        queryset = self.filter_queryset(self.get_queryset())

        # Check for 'pagination' query parameter
        pagination_param = request.query_params.get("pagination", "true")
        if pagination_param.lower() == "false":
            # Return non-paginated response
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        # Return paginated response
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        product = get_object_or_404(Order, pk=pk)
        serializer = self.get_serializer(product)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        product = get_object_or_404(Order, pk=pk)
        serializer = self.get_serializer(product, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        product = get_object_or_404(Order, pk=pk)
        serializer = self.get_serializer(product, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        product = get_object_or_404(Order, pk=pk)
        product.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
