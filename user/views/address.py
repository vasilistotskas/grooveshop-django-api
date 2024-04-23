from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api.throttling import BurstRateThrottle
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from user.models.address import UserAddress
from user.serializers.address import UserAddressSerializer


class UserAddressViewSet(BaseModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = UserAddress.objects.all()
    serializer_class = UserAddressSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = [
        "id",
        "user",
        "country",
        "city",
        "street",
        "zipcode",
        "floor",
        "location_type",
        "is_main",
    ]
    ordering_fields = [
        "id",
        "user",
        "country",
        "zipcode",
        "floor",
        "location_type",
        "is_main",
        "created_at",
        "updated_at",
    ]
    ordering = ["-is_main", "-created_at"]
    search_fields = ["id", "user", "country", "city", "street", "zipcode"]

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        address = self.get_object()
        if address.is_main:
            return Response(
                {"error": "Cannot delete main address"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self.perform_destroy(address)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["POST"], throttle_classes=[BurstRateThrottle])
    def set_main(self, request, pk=None, *args, **kwargs) -> Response:
        main_address = UserAddress.objects.filter(user=request.user, is_main=True)
        if main_address.exists():
            main_address.update(is_main=False)
        address = self.get_object()
        address.is_main = True
        address.save()
        return Response(status=status.HTTP_200_OK)
