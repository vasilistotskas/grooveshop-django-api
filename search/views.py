from django.db.models import Q
from rest_framework import generics

from product.models.product import Product
from product.serializers.product import ProductSerializer
from search.paginators import SearchPagination


class SearchProduct(generics.ListAPIView):
    serializer_class = ProductSerializer
    pagination_class = SearchPagination

    def get_queryset(self):
        queryset = Product.objects.all()
        query = self.request.query_params.get("query")

        if query is not None:
            queryset = queryset.filter(Q(id__icontains=query) | Q(slug__icontains=query))
        return queryset
