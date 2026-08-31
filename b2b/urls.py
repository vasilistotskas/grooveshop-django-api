from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from b2b.views import B2BViewSet

app_name = "b2b"

urlpatterns = [
    path(
        "b2b/profile",
        B2BViewSet.as_view({"get": "profile", "put": "submit_profile"}),
        name="b2b-profile",
    ),
    path(
        "b2b/prices",
        B2BViewSet.as_view({"get": "prices"}),
        name="b2b-prices",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
