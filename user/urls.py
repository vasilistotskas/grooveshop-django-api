from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from user.views.account import UserAccountViewSet
from user.views.address import UserAddressViewSet

urlpatterns = [
    path(
        "user/account",
        UserAccountViewSet.as_view({"get": "list", "post": "create"}),
        name="user-account-list",
    ),
    path(
        "user/account/<int:pk>",
        UserAccountViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="user-account-detail",
    ),
    path(
        "user/account/<int:pk>/favourite_products",
        UserAccountViewSet.as_view({"get": "favourite_products"}),
        name="user-account-favourite-products",
    ),
    path(
        "user/account/<int:pk>/orders",
        UserAccountViewSet.as_view({"get": "orders"}),
        name="user-account-orders",
    ),
    path(
        "user/account/<int:pk>/product_reviews",
        UserAccountViewSet.as_view({"get": "product_reviews"}),
        name="user-account-product-reviews",
    ),
    path(
        "user/account/<int:pk>/addresses",
        UserAccountViewSet.as_view({"get": "addresses"}),
        name="user-account-addresses",
    ),
    path(
        "user/account/<int:pk>/blog_post_comments",
        UserAccountViewSet.as_view({"get": "blog_post_comments"}),
        name="user-account-blog-post-comments",
    ),
    path(
        "user/account/<int:pk>/liked_blog_posts",
        UserAccountViewSet.as_view({"get": "liked_blog_posts"}),
        name="user-account-liked-blog-posts",
    ),
    path(
        "user/account/<int:pk>/liked_blog_posts",
        UserAccountViewSet.as_view({"get": "liked_blog_posts"}),
        name="user-account-liked-blog-posts",
    ),
    path(
        "user/account/<int:pk>/notifications",
        UserAccountViewSet.as_view({"get": "notifications"}),
        name="user-account-notifications",
    ),
    path(
        "user/account/<int:pk>/change_username",
        UserAccountViewSet.as_view({"post": "change_username"}),
        name="user-account-change-username",
    ),
    # Address
    path(
        "user/address",
        UserAddressViewSet.as_view({"get": "list", "post": "create"}),
        name="user-address-list",
    ),
    path(
        "user/address/<int:pk>",
        UserAddressViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="user-address-detail",
    ),
    path(
        "user/address/<int:pk>/set_main",
        UserAddressViewSet.as_view({"post": "set_main"}),
        name="user-address-set-main",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
