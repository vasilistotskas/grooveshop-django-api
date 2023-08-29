from rest_framework import generics
from rest_framework import status
from rest_framework.response import Response

from product.models.product import Product
from product.serializers.product import ProductSerializer
from search.paginators import SearchPagination


class SearchProduct(generics.ListAPIView):
    serializer_class = ProductSerializer
    pagination_class = SearchPagination

    def get_queryset(self):
        queryset = Product.objects.all()
        query = self.request.query_params.get("query", None)
        language = self.request.query_params.get("language", None)

        if language:
            queryset = queryset.filter(translations__language_code=language)

        if query:
            queryset = Product.objects.filter(search_vector=query)
            return queryset

        return queryset.none()

    def list(self, request, *args, **kwargs) -> Response:
        queryset = self.filter_queryset(self.get_queryset())

        if queryset.exists():  # Check if products are found
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
        else:
            return Response("No results found", status=status.HTTP_404_NOT_FOUND)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
