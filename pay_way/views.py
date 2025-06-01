from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.filters import SearchFilter

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods
from pay_way.models import PayWay
from pay_way.serializers import PayWaySerializer


@extend_schema_view(
    list=extend_schema(
        summary=_("List payment methods"),
        description=_(
            "Retrieve a list of payment methods with filtering and search capabilities."
        ),
        tags=["Payment"],
        responses={
            200: PayWaySerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a payment method"),
        description=_("Create a new payment method. Requires authentication."),
        tags=["Payment"],
        responses={
            201: PayWaySerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a payment method"),
        description=_(
            "Get detailed information about a specific payment method."
        ),
        tags=["Payment"],
        responses={
            200: PayWaySerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a payment method"),
        description=_(
            "Update payment method information. Requires authentication."
        ),
        tags=["Payment"],
        responses={
            200: PayWaySerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a payment method"),
        description=_(
            "Partially update payment method information. Requires authentication."
        ),
        tags=["Payment"],
        responses={
            200: PayWaySerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a payment method"),
        description=_("Delete a payment method. Requires authentication."),
        tags=["Payment"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class PayWayViewSet(BaseModelViewSet):
    queryset = PayWay.objects.all()
    serializer_class = PayWaySerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = [
        "active",
        "cost",
        "free_for_order_amount",
        "provider_code",
        "is_online_payment",
        "requires_confirmation",
    ]
    ordering_fields = [
        "cost",
        "free_for_order_amount",
        "created_at",
        "provider_code",
        "is_online_payment",
        "requires_confirmation",
        "sort_order",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "provider_code",
        "translations__name",
        "translations__description",
    ]
