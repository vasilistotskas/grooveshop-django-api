from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods
from slider.models import Slide
from slider.models import Slider
from slider.serializers import SliderSerializer
from slider.serializers import SlideSerializer


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
