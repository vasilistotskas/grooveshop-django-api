from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from user.views.account import UserAccountSessionView
from user.views.account import UserAccountViewSet
from user.views.address import UserAddressViewSet

urlpatterns = [
    path(
        "user/account/",
        UserAccountViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "user/account/<int:pk>/",
        UserAccountViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    path(
        "user/account/session/",
        UserAccountSessionView.as_view(),
    ),
    # Address
    path(
        "user/address/",
        UserAddressViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "user/address/<int:pk>/",
        UserAddressViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    path(
        "user/address/<int:pk>/set_main/",
        UserAddressViewSet.as_view({"post": "set_main"}),
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
