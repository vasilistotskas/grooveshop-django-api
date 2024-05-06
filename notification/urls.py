from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from notification.views.user import NotificationUserViewSet

urlpatterns = [
    path(
        "notification/user",
        NotificationUserViewSet.as_view({"get": "list", "post": "create"}),
        name="notification-user-list",
    ),
    path(
        "notification/user/<str:pk>",
        NotificationUserViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="notification-user-detail",
    ),
    path(
        "notification/user/<str:pk>/unseen_count",
        NotificationUserViewSet.as_view({"get": "unseen_count"}),
        name="notification-user-unseen-count",
    ),
    path(
        "notification/user/<str:pk>/mark_all_as_seen",
        NotificationUserViewSet.as_view({"post": "mark_all_as_seen"}),
        name="notification-user-mark-all-as-seen",
    ),
    path(
        "notification/user/<str:pk>/mark_all_as_unseen",
        NotificationUserViewSet.as_view({"post": "mark_all_as_unseen"}),
        name="notification-user-mark-all-as-unseen",
    ),
    path(
        "notification/user/<str:pk>/mark_as_seen",
        NotificationUserViewSet.as_view({"post": "mark_as_seen"}),
        name="notification-user-mark-as-seen",
    ),
    path(
        "notification/user/<str:pk>/mark_as_unseen",
        NotificationUserViewSet.as_view({"post": "mark_as_unseen"}),
        name="notification-user-mark-as-unseen",
    ),
]


urlpatterns = format_suffix_patterns(urlpatterns)
