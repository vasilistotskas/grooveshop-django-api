from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
)
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods
from user.filters.address import UserAddressFilter
from user.models.address import UserAddress
from user.serializers.address import (
    BulkDeleteAddressesRequestSerializer,
    BulkDeleteAddressesResponseSerializer,
    UserAddressDetailSerializer,
    UserAddressSerializer,
    UserAddressWriteSerializer,
    ValidateAddressResponseSerializer,
)


@extend_schema_view(
    **create_schema_view_config(
        model_class=UserAddress,
        display_config={
            "tag": "User Addresses",
        },
        serializers={
            "list_serializer": UserAddressSerializer,
            "detail_serializer": UserAddressDetailSerializer,
            "write_serializer": UserAddressWriteSerializer,
        },
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
    validate_address=extend_schema(
        operation_id="validateUserAddress",
        summary=_("Validate address"),
        description=_("Validate an address without saving it."),
        tags=["User Addresses"],
        request=UserAddressWriteSerializer,
        responses={
            200: ValidateAddressResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    bulk_delete=extend_schema(
        operation_id="bulkDeleteUserAddresses",
        summary=_("Bulk delete addresses"),
        description=_("Delete multiple addresses by their IDs."),
        tags=["User Addresses"],
        request=BulkDeleteAddressesRequestSerializer,
        responses={
            200: BulkDeleteAddressesResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class UserAddressViewSet(MultiSerializerMixin, BaseModelViewSet):
    serializer_class = None
    serializers = {
        "default": UserAddressDetailSerializer,
        "list": UserAddressSerializer,
        "retrieve": UserAddressDetailSerializer,
        "create": UserAddressWriteSerializer,
        "update": UserAddressWriteSerializer,
        "partial_update": UserAddressWriteSerializer,
        "set_main": UserAddressDetailSerializer,
        "get_main": UserAddressDetailSerializer,
        "validate_address": ValidateAddressResponseSerializer,
        "bulk_delete": BulkDeleteAddressesResponseSerializer,
    }
    permission_classes = [IsAuthenticated]
    filterset_class = UserAddressFilter
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
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
        if getattr(self, "swagger_fake_view", False):
            return UserAddress.objects.none()

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

        serializer = self.get_serializer(address)
        return Response(serializer.data)

    @action(detail=False, methods=["GET"])
    def get_main(self, request):
        main_address = UserAddress.objects.filter(
            user=request.user, is_main=True
        ).first()
        if main_address:
            serializer = self.get_serializer(main_address)
            return Response(serializer.data)
        return Response({"detail": _("No main address found.")}, status=404)

    @action(detail=False, methods=["POST"])
    def validate_address(self, request):
        serializer = UserAddressWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return Response(
            {
                "valid": True,
                "details": _("Address validation successful."),
                "data": serializer.validated_data,
            }
        )

    @action(detail=False, methods=["DELETE"])
    def bulk_delete(self, request):
        serializer = BulkDeleteAddressesRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        address_ids = serializer.validated_data["address_ids"]
        deleted_count, _ = UserAddress.objects.filter(
            id__in=address_ids, user=request.user
        ).delete()

        return Response(
            {
                "deleted_count": deleted_count,
                "requested_count": len(address_ids),
                "details": _(
                    f"Deleted {deleted_count} out of {len(address_ids)} requested addresses."
                ),
            }
        )
