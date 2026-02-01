from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from search.views import (
    blog_post_meili_search,
    federated_search,
    product_meili_search,
    search_analytics,
)

urlpatterns = [
    path(
        "search/blog/post",
        blog_post_meili_search,
        name="search-blog-post",
    ),
    path(
        "search/product",
        product_meili_search,
        name="search-product",
    ),
    path(
        "search/federated",
        federated_search,
        name="search-federated",
    ),
    path(
        "search/analytics",
        search_analytics,
        name="search-analytics",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
