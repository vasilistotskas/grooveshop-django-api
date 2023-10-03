from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from session.views import active_users_count
from session.views import all_sessions
from session.views import refresh_last_activity
from session.views import refresh_session
from session.views import revoke_all_user_sessions
from session.views import revoke_user_session
from session.views import session_view

urlpatterns = [
    path("auth/session/", session_view, name="session"),
    path("auth/session/all/", all_sessions, name="session-all"),
    path("auth/session/refresh/", refresh_session, name="session-refresh"),
    path(
        "auth/session/revoke/<str:session_key>/",
        revoke_user_session,
        name="session-revoke",
    ),
    path(
        "auth/session/revoke/all/",
        revoke_all_user_sessions,
        name="session-revoke-all",
    ),
    path(
        "auth/session/active_users_count/",
        active_users_count,
        name="session-active-users-count",
    ),
    path(
        "auth/session/refresh_last_activity/",
        refresh_last_activity,
        name="session-refresh-last-activity",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
