from django.urls import path

from builder import media_api, session_api, views

app_name = "builder"

urlpatterns = [
    path(
        "builder/documents",
        views.DocumentListView.as_view(),
        name="document-list",
    ),
    path(
        "builder/documents/<str:key>",
        views.DocumentView.as_view(),
        name="document-detail",
    ),
    path(
        "builder/documents/<str:key>/draft",
        views.DraftView.as_view(),
        name="document-draft",
    ),
    path(
        "builder/documents/<str:key>/discard",
        views.DiscardView.as_view(),
        name="document-discard",
    ),
    path(
        "builder/documents/<str:key>/checkpoint",
        views.CheckpointView.as_view(),
        name="document-checkpoint",
    ),
    path(
        "builder/documents/<str:key>/versions",
        views.VersionListView.as_view(),
        name="version-list",
    ),
    path(
        "builder/documents/<str:key>/versions/<int:number>",
        views.VersionView.as_view(),
        name="version-detail",
    ),
    path(
        "builder/documents/<str:key>/versions/<int:number>/restore",
        views.RestoreView.as_view(),
        name="version-restore",
    ),
    path(
        "builder/media",
        media_api.AssetListView.as_view(),
        name="media-list",
    ),
    path(
        "builder/media/resolve",
        media_api.ResolvedMediaView.as_view(),
        name="media-resolve",
    ),
    path(
        "builder/media/<int:pk>",
        media_api.AssetDetailView.as_view(),
        name="media-detail",
    ),
    path(
        "builder/staff-session",
        session_api.StaffSessionView.as_view(),
        name="staff-session",
    ),
    path("builder/publish", views.PublishView.as_view(), name="publish"),
    path("builder/rollback", views.RollbackView.as_view(), name="rollback"),
    path(
        "builder/releases",
        views.ReleaseListView.as_view(),
        name="release-list",
    ),
    path(
        "builder/published/<slug:page_key>",
        views.PublishedPageView.as_view(),
        name="published",
    ),
]
