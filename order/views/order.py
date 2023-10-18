from django.contrib.auth import get_user_model
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from order.models.order import Order
from order.paginators.order import OrderListPagination
from order.serializers.order import CheckoutSerializer
from order.serializers.order import OrderCreateUpdateSerializer
from order.serializers.order import OrderSerializer

User = get_user_model()


class Checkout(APIView):
    serializer_class = CheckoutSerializer
    queryset = Order.objects.all()

    def create_order(self, request: Request, serializer: CheckoutSerializer) -> None:
        user_id = request.data.get("user_id")
        user = User.objects.get(id=user_id) if user_id else None
        serializer.save(user=user)

    def post(self, request, format=None):
        serializer = CheckoutSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        self.create_order(request, serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class OrderViewSet(MultiSerializerMixin, ModelViewSet):
    queryset = Order.objects.all()
    pagination_class = OrderListPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    ordering_fields = ["created_at", "status"]
    filterset_fields = ["user_id", "status"]
    ordering = ["-created_at"]
    search_fields = ["user__email", "user_id"]

    serializers = {
        "default": OrderSerializer,
        "list": OrderSerializer,
        "create": OrderCreateUpdateSerializer,
        "retrieve": OrderSerializer,
        "update": OrderCreateUpdateSerializer,
        "partial_update": OrderCreateUpdateSerializer,
        "destroy": OrderSerializer,
    }

    def list(self, request, *args, **kwargs) -> Response:
        queryset = self.filter_queryset(self.get_queryset())
        pagination_param = request.query_params.get("pagination", "true")

        if pagination_param.lower() == "false":
            # Return non-paginated response
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

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

    @action(detail=True, methods=["GET"])
    def retrieve_by_uuid(self, request, uuid=None, *args, **kwargs) -> Response:
        product = get_object_or_404(Order, uuid=uuid)
        serializer = self.get_serializer(product)
        return Response(serializer.data, status=status.HTTP_200_OK)
