from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.api.views import BaseExpandView
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from slider.models import Slide
from slider.models import Slider
from slider.paginators import SlidePagination
from slider.paginators import SliderPagination
from slider.serializers import SliderSerializer
from slider.serializers import SlideSerializer

DEFAULT_SLIDER_CACHE_TTL = 60 * 60 * 2
DEFAULT_SLIDE_CACHE_TTL = 60 * 60 * 2


class SliderViewSet(ModelViewSet):
    queryset = Slider.objects.all()
    serializer_class = SliderSerializer
    pagination_class = SliderPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id"]
    ordering_fields = ["id", "created_at"]
    ordering = ["id"]
    search_fields = ["id"]

    @method_decorator(cache_page(DEFAULT_SLIDER_CACHE_TTL))
    def list(self, request, *args, **kwargs) -> Response:
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @method_decorator(cache_page(DEFAULT_SLIDER_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        slider = get_object_or_404(Slider, pk=pk)
        serializer = self.get_serializer(slider)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        slider = get_object_or_404(Slider, pk=pk)
        serializer = self.get_serializer(slider, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        slider = get_object_or_404(Slider, pk=pk)
        serializer = self.get_serializer(slider, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        slider = get_object_or_404(Slider, pk=pk)
        slider.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SlideViewSet(BaseExpandView, ModelViewSet):
    queryset = Slide.objects.all()
    serializer_class = SlideSerializer
    pagination_class = SlidePagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "slider"]
    ordering_fields = ["id", "slider", "created_at"]
    ordering = ["id"]
    search_fields = ["id"]

    @method_decorator(cache_page(DEFAULT_SLIDE_CACHE_TTL))
    def list(self, request, *args, **kwargs) -> Response:
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @method_decorator(cache_page(DEFAULT_SLIDE_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        slide = get_object_or_404(Slide, pk=pk)
        serializer = self.get_serializer(slide)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        slide = get_object_or_404(Slide, pk=pk)
        serializer = self.get_serializer(slide, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        slide = get_object_or_404(Slide, pk=pk)
        serializer = self.get_serializer(slide, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        slide = get_object_or_404(Slide, pk=pk)
        slide.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
