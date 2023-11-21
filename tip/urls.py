from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from tip.views import TipViewSet

urlpatterns = [
    path(
        "tip",
        TipViewSet.as_view({"get": "list", "post": "create"}),
        name="tip-list",
    ),
    path(
        "tip/<str:pk>",
        TipViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="tip-detail",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
