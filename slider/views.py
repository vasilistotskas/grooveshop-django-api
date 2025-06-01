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
from slider.models import Slide, Slider
from slider.serializers import SliderSerializer, SlideSerializer


@extend_schema_view(
    list=extend_schema(
        summary=_("List sliders"),
        description=_(
            "Retrieve a list of sliders with filtering and search capabilities."
        ),
        tags=["Sliders"],
        responses={
            200: SliderSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a slider"),
        description=_("Create a new slider. Requires authentication."),
        tags=["Sliders"],
        responses={
            201: SliderSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a slider"),
        description=_("Get detailed information about a specific slider."),
        tags=["Sliders"],
        responses={
            200: SliderSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a slider"),
        description=_("Update slider information. Requires authentication."),
        tags=["Sliders"],
        responses={
            200: SliderSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a slider"),
        description=_(
            "Partially update slider information. Requires authentication."
        ),
        tags=["Sliders"],
        responses={
            200: SliderSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a slider"),
        description=_("Delete a slider. Requires authentication."),
        tags=["Sliders"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class SliderViewSet(BaseModelViewSet):
    queryset = Slider.objects.all()
    serializer_class = SliderSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id"]
    ordering_fields = ["id", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id"]


@extend_schema_view(
    list=extend_schema(
        summary=_("List slides"),
        description=_(
            "Retrieve a list of slides with filtering and search capabilities."
        ),
        tags=["Slides"],
        responses={
            200: SlideSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a slide"),
        description=_(
            "Create a new slide for a slider. Requires authentication."
        ),
        tags=["Slides"],
        responses={
            201: SlideSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a slide"),
        description=_("Get detailed information about a specific slide."),
        tags=["Slides"],
        responses={
            200: SlideSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a slide"),
        description=_("Update slide information. Requires authentication."),
        tags=["Slides"],
        responses={
            200: SlideSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a slide"),
        description=_(
            "Partially update slide information. Requires authentication."
        ),
        tags=["Slides"],
        responses={
            200: SlideSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a slide"),
        description=_("Delete a slide. Requires authentication."),
        tags=["Slides"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class SlideViewSet(BaseModelViewSet):
    queryset = Slide.objects.all()
    serializer_class = SlideSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "slider"]
    ordering_fields = ["id", "slider", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id"]
