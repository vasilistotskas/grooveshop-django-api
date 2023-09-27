from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from session.views import active_users_count
from session.views import clear_all_user_sessions
from session.views import refresh_last_activity
from session.views import session_view

urlpatterns = [
    path("session/", session_view, name="session"),
    path(
        "session/clear_all/",
        clear_all_user_sessions,
        name="session-clear-all",
    ),
    path(
        "session/active_users_count/",
        active_users_count,
        name="session-active-users-count",
    ),
    path(
        "session/refresh_last_activity/",
        refresh_last_activity,
        name="session-refresh-last-activity",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
