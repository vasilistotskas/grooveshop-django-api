from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from search.views import blog_post_meili_search
from search.views import product_meili_search

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
]

urlpatterns = format_suffix_patterns(urlpatterns)
