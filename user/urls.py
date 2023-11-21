from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from user.views.account import UserAccountSessionView
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
        "user/account/session",
        UserAccountSessionView.as_view(),
        name="user-account-session",
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
