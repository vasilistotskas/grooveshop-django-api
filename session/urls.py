from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from session.views import ActiveUserViewSet
from session.views import ClearAllUserSessions
from session.views import session_view

urlpatterns = [
    path("session/", session_view, name="api-session"),
    path(
        "session/clear_all/",
        ClearAllUserSessions.as_view(),
        name="api-session-clear-all",
    ),
    path(
        "active_users/active_users_count/",
        ActiveUserViewSet.as_view({"get": "active_users_count"}),
        name="active-user-active-users-count",
    ),
    path(
        "active_users/refresh_last_activity/",
        ActiveUserViewSet.as_view({"post": "refresh_last_activity"}),
        name="active-user-refresh-last-activity",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
