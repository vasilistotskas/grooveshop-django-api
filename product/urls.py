from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from product.views.category import ProductCategoryViewSet
from product.views.favourite import ProductFavouriteViewSet
from product.views.images import ProductImagesViewSet
from product.views.product import ProductViewSet
from product.views.review import ProductReviewViewSet

urlpatterns = [
    # Product
    path(
        "product/",
        ProductViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "product/<int:pk>/",
        ProductViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    path(
        "product/<int:pk>/update_product_hits/",
        ProductViewSet.as_view({"post": "update_product_hits"}),
    ),
    # Category
    path(
        "product/category/",
        ProductCategoryViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "product/category/<int:pk>/",
        ProductCategoryViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    # Favourite
    path(
        "product/favourite/",
        ProductFavouriteViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "product/favourite/<str:pk>/",
        ProductFavouriteViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    # Review
    path(
        "product/review/",
        ProductReviewViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "product/review/<int:pk>/",
        ProductReviewViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    path(
        "product/review/user_had_reviewed/",
        ProductReviewViewSet.as_view({"post": "user_had_reviewed"}),
    ),
    # Images
    path(
        "product/images/",
        ProductImagesViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "product/images/<int:pk>/",
        ProductImagesViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
]


urlpatterns = format_suffix_patterns(urlpatterns)
