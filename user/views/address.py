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
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import cache_methods
from user.filters.address import UserAddressFilter
from user.models.address import UserAddress
from user.serializers.address import (
    BulkDeleteAddressesRequestSerializer,
    BulkDeleteAddressesResponseSerializer,
    UserAddressDetailSerializer,
    UserAddressListSerializer,
    UserAddressWriteSerializer,
    ValidateAddressResponseSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary=_("List user addresses"),
        description=_(
            "Retrieve a list of addresses for the authenticated user."
        ),
        tags=["User Addresses"],
        responses={
            200: UserAddressListSerializer(many=True),
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a user address"),
        description=_(
            "Get detailed information about a specific user address."
        ),
        tags=["User Addresses"],
        responses={
            200: UserAddressDetailSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    create=extend_schema(
        summary=_("Create a user address"),
        description=_("Create a new address for the authenticated user."),
        tags=["User Addresses"],
        request=UserAddressWriteSerializer,
        responses={
            201: UserAddressDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a user address"),
        description=_("Update user address information."),
        tags=["User Addresses"],
        request=UserAddressWriteSerializer,
        responses={
            200: UserAddressDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a user address"),
        description=_("Partially update user address information."),
        tags=["User Addresses"],
        request=UserAddressWriteSerializer,
        responses={
            200: UserAddressDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a user address"),
        description=_("Delete a user address."),
        tags=["User Addresses"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    set_main=extend_schema(
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
    serializers = {
        "list": UserAddressListSerializer,
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
        ).select_related("user", "country", "region")

    @action(detail=True, methods=["POST"])
    def set_main(self, request, pk=None):
        address = self.get_object()

        UserAddress.objects.filter(user=request.user, is_main=True).update(
            is_main=False
        )

        address.is_main = True
        address.save(update_fields=["is_main"])

        serializer = self.get_serializer(address)
        return Response(serializer.data)

    @action(detail=False, methods=["GET"])
    def get_main(self, request):
        try:
            main_address = self.get_queryset().get(is_main=True)
            serializer = self.get_serializer(main_address)
            return Response(serializer.data)
        except UserAddress.DoesNotExist:
            return Response({"detail": _("No main address found.")}, status=404)

    @action(detail=False, methods=["POST"])
    def validate_address(self, request):
        serializer = UserAddressWriteSerializer(
            data=request.data, context=self.get_serializer_context()
        )

        if serializer.is_valid():
            # Here we could integrate with address validation services
            # like Google Maps API, SmartyStreets, etc.
            return Response({"valid": True, "errors": {}, "suggestions": []})
        else:
            return Response(
                {"valid": False, "errors": serializer.errors, "suggestions": []}
            )

    @action(detail=False, methods=["DELETE"])
    def bulk_delete(self, request):
        address_ids = request.data.get("address_ids", [])

        if not address_ids:
            return Response(
                {"detail": _("No address IDs provided.")}, status=400
            )

        queryset = self.get_queryset().filter(id__in=address_ids)
        deleted_ids = list(queryset.values_list("id", flat=True))
        deleted_count = queryset.count()

        queryset.delete()

        return Response(
            {"deleted_count": deleted_count, "deleted_ids": deleted_ids}
        )
