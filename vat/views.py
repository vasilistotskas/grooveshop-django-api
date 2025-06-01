from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.filters import SearchFilter

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from vat.models import Vat
from vat.serializers import VatSerializer


@extend_schema_view(
    list=extend_schema(
        summary=_("List VAT rates"),
        description=_(
            "Retrieve a list of VAT rates with filtering and search capabilities."
        ),
        tags=["Vat"],
        responses={
            200: VatSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a VAT rate"),
        description=_("Create a new VAT rate. Requires authentication."),
        tags=["Vat"],
        responses={
            201: VatSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a VAT rate"),
        description=_("Get detailed information about a specific VAT rate."),
        tags=["Vat"],
        responses={
            200: VatSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a VAT rate"),
        description=_("Update VAT rate information. Requires authentication."),
        tags=["Vat"],
        responses={
            200: VatSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a VAT rate"),
        description=_(
            "Partially update VAT rate information. Requires authentication."
        ),
        tags=["Vat"],
        responses={
            200: VatSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a VAT rate"),
        description=_("Delete a VAT rate. Requires authentication."),
        tags=["Vat"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
class VatViewSet(BaseModelViewSet):
    queryset = Vat.objects.all()
    serializer_class = VatSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "value"]
    ordering_fields = ["id", "value", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id", "value"]
