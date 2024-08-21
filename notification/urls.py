from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from notification.views.notification import notifications_by_ids
from notification.views.user import NotificationUserViewSet

urlpatterns = [
    path(
        "notification/ids",
        notifications_by_ids,
        name="notifications-by-ids",
    ),
    path(
        "notification/user/unseen_count",
        NotificationUserViewSet.as_view({"get": "unseen_count"}),
        name="notification-user-unseen-count",
    ),
    path(
        "notification/user/mark_all_as_seen",
        NotificationUserViewSet.as_view({"post": "mark_all_as_seen"}),
        name="notification-user-mark-all-as-seen",
    ),
    path(
        "notification/user/mark_all_as_unseen",
        NotificationUserViewSet.as_view({"post": "mark_all_as_unseen"}),
        name="notification-user-mark-all-as-unseen",
    ),
    path(
        "notification/user/mark_as_seen",
        NotificationUserViewSet.as_view({"post": "mark_as_seen"}),
        name="notification-user-mark-as-seen",
    ),
    path(
        "notification/user/mark_as_unseen",
        NotificationUserViewSet.as_view({"post": "mark_as_unseen"}),
        name="notification-user-mark-as-unseen",
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
]


urlpatterns = format_suffix_patterns(urlpatterns)
