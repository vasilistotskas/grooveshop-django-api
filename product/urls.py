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
        "product/<int:pk>/update_view_count",
        ProductViewSet.as_view({"post": "update_view_count"}),
        name="product-update-view-count",
    ),
    path(
        "product/<int:pk>/reviews",
        ProductViewSet.as_view({"get": "reviews"}),
        name="product-reviews",
    ),
    path(
        "product/<int:pk>/images",
        ProductViewSet.as_view({"get": "images"}),
        name="product-images",
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
        "product/favourite/favourites_by_products",
        ProductFavouriteViewSet.as_view({"post": "favourites_by_products"}),
        name="product-favourite-favourites-by-products",
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
    path(
        "product/favourite/<str:pk>/product",
        ProductFavouriteViewSet.as_view({"get": "product"}),
        name="product-favourite-product",
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
        "product/review/user_product_review",
        ProductReviewViewSet.as_view({"post": "user_product_review"}),
        name="product-review-user-product-review",
    ),
    path(
        "product/review/<str:pk>/product",
        ProductReviewViewSet.as_view({"get": "product"}),
        name="product-review-product",
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
