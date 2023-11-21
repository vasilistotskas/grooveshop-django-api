from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from product.views.category import ProductCategoryViewSet
from product.views.favourite import ProductFavouriteViewSet
from product.views.image import ProductImageViewSet
from product.views.product import ProductViewSet
from product.views.review import ProductReviewViewSet

urlpatterns = [
    # Product
    path(
        "product",
        ProductViewSet.as_view({"get": "list", "post": "create"}),
        name="product-list",
    ),
    path(
        "product/<int:pk>",
        ProductViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="product-detail",
    ),
    path(
        "product/<int:pk>/update_product_hits",
        ProductViewSet.as_view({"post": "update_product_hits"}),
        name="product-update-product-hits",
    ),
    # Category
    path(
        "product/category",
        ProductCategoryViewSet.as_view({"get": "list", "post": "create"}),
        name="product-category-list",
    ),
    path(
        "product/category/<int:pk>",
        ProductCategoryViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="product-category-detail",
    ),
    # Favourite
    path(
        "product/favourite",
        ProductFavouriteViewSet.as_view({"get": "list", "post": "create"}),
        name="product-favourite-list",
    ),
    path(
        "product/favourite/<str:pk>",
        ProductFavouriteViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="product-favourite-detail",
    ),
    # Review
    path(
        "product/review",
        ProductReviewViewSet.as_view({"get": "list", "post": "create"}),
        name="product-review-list",
    ),
    path(
        "product/review/<int:pk>",
        ProductReviewViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="product-review-detail",
    ),
    path(
        "product/review/user_had_reviewed",
        ProductReviewViewSet.as_view({"post": "user_had_reviewed"}),
        name="product-review-user-had-reviewed",
    ),
    # Images
    path(
        "product/image",
        ProductImageViewSet.as_view({"get": "list", "post": "create"}),
        name="product-image-list",
    ),
    path(
        "product/image/<int:pk>",
        ProductImageViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="product-image-detail",
    ),
]


urlpatterns = format_suffix_patterns(urlpatterns)
