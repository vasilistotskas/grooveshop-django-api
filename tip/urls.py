from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from tip.views import TipViewSet

urlpatterns = [
    path(
        "tip/",
        TipViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "tip/<str:pk>/",
        TipViewSet.as_view(
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
