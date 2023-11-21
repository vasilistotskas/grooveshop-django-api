from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from search.views import SearchProduct

urlpatterns = [
    path(
        "search/product",
        SearchProduct.as_view({"get": "list"}),
        name="search-product",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
