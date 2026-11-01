from django.urls import path

from builder import views

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
