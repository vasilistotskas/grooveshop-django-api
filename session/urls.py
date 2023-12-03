from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from session.views import active_users_count

urlpatterns = [
    path(
        "auth/session/active_users_count",
        active_users_count,
        name="session-active-users-count",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
