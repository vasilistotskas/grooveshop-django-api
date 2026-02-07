from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
from rest_framework.decorators import action

from rest_framework.response import Response

from core.api.permissions import IsOwnerOrAdmin
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.utils.views import cache_methods
from user.filters.address import UserAddressFilter
from user.models.address import UserAddress
from user.serializers.address import (
    UserAddressDetailSerializer,
    UserAddressSerializer,
    UserAddressWriteSerializer,
)

serializers_config: SerializersConfig = {
    **crud_config(
        list=UserAddressSerializer,
        detail=UserAddressDetailSerializer,
        write=UserAddressWriteSerializer,
    ),
    "set_main": ActionConfig(
        response=UserAddressDetailSerializer,
        operation_id="setMainUserAddress",
        summary=_("Set address as main"),
        description=_("Set this address as the user's main address."),
        tags=["User Addresses"],
    ),
    "get_main": ActionConfig(
        response=UserAddressDetailSerializer,
        operation_id="getMainUserAddress",
        summary=_("Get main address"),
        description=_("Retrieve the user's main address."),
        tags=["User Addresses"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=UserAddress,
        display_config={
            "tag": "User Addresses",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class UserAddressViewSet(BaseModelViewSet):
    queryset = UserAddress.objects.none()
    serializers_config = serializers_config
    permission_classes = [IsOwnerOrAdmin]
    filterset_class = UserAddressFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "city",
        "zipcode",
        "is_main",
    ]
    ordering = ["-is_main", "-created_at"]
    search_fields = [
        "title",
        "first_name",
        "last_name",
        "street",
        "street_number",
        "city",
        "zipcode",
        "notes",
    ]

    def get_queryset(self):
        if self.action == "list":
            return UserAddress.objects.for_list().filter(user=self.request.user)
        return UserAddress.objects.for_detail().filter(user=self.request.user)

    @action(detail=True, methods=["POST"])
    def set_main(self, request, pk=None):
        address = self.get_object()
        UserAddress.objects.filter(user=request.user, is_main=True).update(
            is_main=False
        )
        address.is_main = True
        address.save()

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(address)
        return Response(response_serializer.data)

    @action(detail=False, methods=["GET"])
    def get_main(self, request):
        main_address = UserAddress.objects.filter(
            user=request.user, is_main=True
        ).first()
        if main_address:
            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(main_address)
            return Response(response_serializer.data)
        return Response({"detail": _("No main address found.")}, status=404)
