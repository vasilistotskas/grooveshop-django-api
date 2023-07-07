from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from region.views import RegionViewSet

urlpatterns = [
    path(
        "region/",
        RegionViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "region/<str:pk>/",
        RegionViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    path(
        "region/<str:pk>/get_regions_by_country_alpha_2/",
        RegionViewSet.as_view({"get": "get_regions_by_country_alpha_2"}),
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
