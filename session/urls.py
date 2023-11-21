from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from session.views import active_users_count
from session.views import revoke_all_user_sessions
from session.views import revoke_session
from session.views import session_view

urlpatterns = [
    path("auth/session", session_view, name="session"),
    path(
        "auth/session/revoke",
        revoke_session,
        name="session-revoke",
    ),
    path(
        "auth/session/revoke/all",
        revoke_all_user_sessions,
        name="session-revoke-all",
    ),
    path(
        "auth/session/active_users_count",
        active_users_count,
        name="session-active-users-count",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
