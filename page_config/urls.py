from django.urls import path

from page_config.views import (
    ContentPageViewSet,
    NavigationMenuAdminViewSet,
    PageLayoutAdminViewSet,
    public_navigation,
    public_page_config,
)

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
    # Fixed routes must come before the catch-all <str:page_type>
    path(
        "page-config/navigation",
        public_navigation,
        name="page-config-navigation",
    ),
    path(
        "page-config/navigation/admin",
        NavigationMenuAdminViewSet.as_view({"get": "list", "post": "create"}),
        name="page-config-navigation-admin-list",
    ),
    path(
        "page-config/navigation/admin/<int:pk>",
        NavigationMenuAdminViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="page-config-navigation-admin-detail",
    ),
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
