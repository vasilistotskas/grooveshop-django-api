from django.urls import path

from page_config.views import ContentPageViewSet

app_name = "page_config"

urlpatterns = [
    path(
        "content-page",
        ContentPageViewSet.as_view({"get": "list", "post": "create"}),
        name="content-page-list",
    ),
    path(
        "content-page/<slug:slug>",
        ContentPageViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="content-page-detail",
    ),
]
