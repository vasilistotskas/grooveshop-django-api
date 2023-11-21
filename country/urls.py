from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from country.views import CountryViewSet

urlpatterns = [
    path(
        "country",
        CountryViewSet.as_view({"get": "list", "post": "create"}),
        name="country-list",
    ),
    path(
        "country/<str:pk>",
        CountryViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="country-detail",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
