from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from vat.views import VatViewSet

urlpatterns = [
    path(
        "vat/",
        VatViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "vat/<int:pk>/",
        VatViewSet.as_view(
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
