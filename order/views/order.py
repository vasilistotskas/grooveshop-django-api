from django.contrib.auth import get_user_model
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from order.models.order import Order
from order.serializers.order import CheckoutSerializer
from order.serializers.order import OrderCreateUpdateSerializer
from order.serializers.order import OrderSerializer

User = get_user_model()


class Checkout(APIView):
    serializer_class = CheckoutSerializer
    queryset = Order.objects.all()

    def create_order(self, request: Request, serializer: CheckoutSerializer) -> None:
        user = request.user if request.user.is_authenticated else None
        serializer.save(user=user)

    def post(self, request, format=None):
        serializer = CheckoutSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        self.create_order(request, serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class OrderViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = Order.objects.all()
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    ordering_fields = ["created_at", "status"]
    filterset_fields = ["user_id", "status"]
    ordering = ["-created_at"]
    search_fields = ["user__email", "user__username", "user_id"]

    serializers = {
        "default": OrderSerializer,
        "create": OrderCreateUpdateSerializer,
        "update": OrderCreateUpdateSerializer,
        "partial_update": OrderCreateUpdateSerializer,
    }

    @action(detail=True, methods=["GET"])
    def retrieve_by_uuid(self, request, uuid=None, *args, **kwargs) -> Response:
        product = get_object_or_404(Order, uuid=uuid)
        serializer = self.get_serializer(product)
        return Response(serializer.data, status=status.HTTP_200_OK)
