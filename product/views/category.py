from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from product.models.category import ProductCategory
from product.paginators.category import ProductCategoryPagination
from product.serializers.category import ProductCategorySerializer


class ProductCategoryViewSet(ModelViewSet):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer
    pagination_class = ProductCategoryPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "translations__name"]
    ordering_fields = [
        "id",
        "translations__name",
        "created_at",
    ]
    ordering = ["id"]
    search_fields = ["id", "translations__name"]

    def list(self, request, *args, **kwargs) -> Response:
        queryset = self.get_queryset().get_cached_trees()
        page = self.paginate_queryset(queryset)
        data = []
        for n in queryset:
            data.append(self.recursive_node_to_dict(n))
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        category = get_object_or_404(ProductCategory, id=pk)
        serializer = self.get_serializer(category)
        return Response(serializer.data)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        category = get_object_or_404(ProductCategory, id=pk)
        serializer = self.get_serializer(category, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        category = get_object_or_404(ProductCategory, id=pk)
        serializer = self.get_serializer(category, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        category = get_object_or_404(ProductCategory, id=pk)
        category.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def recursive_node_to_dict(self, node):
        result = self.get_serializer(instance=node).data
        children = [self.recursive_node_to_dict(c) for c in node.get_children()]
        if children:
            result["children"] = children
        return result
