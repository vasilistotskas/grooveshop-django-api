from django.urls import path

from page_config.views import PageLayoutAdminViewSet, public_page_config

app_name = "page_config"

urlpatterns = [
    # Admin routes must come before the catch-all <str:page_type>
    path(
        "page-config/admin",
        PageLayoutAdminViewSet.as_view({"get": "list", "post": "create"}),
        name="page-config-admin-list",
    ),
    path(
        "page-config/admin/<int:pk>",
        PageLayoutAdminViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="page-config-admin-detail",
    ),
    path(
        "page-config/<str:page_type>",
        public_page_config,
        name="page-config-public",
    ),
]
