from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from region.views import RegionViewSet

urlpatterns = [
    path(
        "region",
        RegionViewSet.as_view({"get": "list", "post": "create"}),
        name="region-list",
    ),
    path(
        "region/<str:pk>",
        RegionViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="region-detail",
    ),
    path(
        "region/<str:pk>/get_regions_by_country_alpha_2",
        RegionViewSet.as_view({"get": "get_regions_by_country_alpha_2"}),
        name="region-get-regions-by-country-alpha-2",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
