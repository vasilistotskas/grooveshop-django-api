from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
)
from rest_framework.decorators import action

from rest_framework.response import Response

from core.api.permissions import IsOwnerOrAdmin
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods
from user.filters.address import UserAddressFilter
from user.models.address import UserAddress
from user.serializers.address import (
    UserAddressDetailSerializer,
    UserAddressSerializer,
    UserAddressWriteSerializer,
)

req_serializers: RequestSerializersConfig = {
    "create": UserAddressWriteSerializer,
    "update": UserAddressWriteSerializer,
    "partial_update": UserAddressWriteSerializer,
    "set_main": None,
}

res_serializers: ResponseSerializersConfig = {
    "create": UserAddressDetailSerializer,
    "list": UserAddressSerializer,
    "retrieve": UserAddressDetailSerializer,
    "update": UserAddressDetailSerializer,
    "partial_update": UserAddressDetailSerializer,
    "set_main": UserAddressDetailSerializer,
    "get_main": UserAddressDetailSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=UserAddress,
        display_config={
            "tag": "User Addresses",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
        error_serializer=ErrorResponseSerializer,
    ),
    set_main=extend_schema(
        operation_id="setMainUserAddress",
        summary=_("Set address as main"),
        description=_("Set this address as the user's main address."),
        tags=["User Addresses"],
        request=None,
        responses={
            200: UserAddressDetailSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    get_main=extend_schema(
        operation_id="getMainUserAddress",
        summary=_("Get main address"),
        description=_("Retrieve the user's main address."),
        tags=["User Addresses"],
        responses={
            200: UserAddressDetailSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class UserAddressViewSet(BaseModelViewSet):
    queryset = UserAddress.objects.none()
    response_serializers = res_serializers
    request_serializers = req_serializers
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
        return UserAddress.objects.filter(
            user=self.request.user
        ).select_related("country", "region")

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
