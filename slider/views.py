from __future__ import annotations

from django.utils.decorators import method_decorator
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import conditional_cache_page
from slider.models import Slide
from slider.models import Slider
from slider.serializers import SliderSerializer
from slider.serializers import SlideSerializer

DEFAULT_SLIDER_CACHE_TTL = 60 * 60 * 2
DEFAULT_SLIDE_CACHE_TTL = 60 * 60 * 2


class SliderViewSet(BaseModelViewSet):
    queryset = Slider.objects.all()
    serializer_class = SliderSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id"]
    ordering_fields = ["id", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id"]

    @method_decorator(conditional_cache_page(DEFAULT_SLIDER_CACHE_TTL))
    def list(self, request, *args, **kwargs) -> Response:
        return super().list(request, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_SLIDER_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        return super().retrieve(request, pk=pk)


class SlideViewSet(BaseModelViewSet):
    queryset = Slide.objects.all()
    serializer_class = SlideSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "slider"]
    ordering_fields = ["id", "slider", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id"]

    @method_decorator(conditional_cache_page(DEFAULT_SLIDE_CACHE_TTL))
    def list(self, request, *args, **kwargs) -> Response:
        return super().list(request, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_SLIDE_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        return super().retrieve(request, pk=pk)
