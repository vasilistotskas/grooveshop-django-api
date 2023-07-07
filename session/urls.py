from session.views import ClearAllUserSessions
from session.views import session_view
from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

urlpatterns = [
    path("session/", session_view, name="api-session"),
    path("session/clear_all/", ClearAllUserSessions.as_view()),
]

urlpatterns = format_suffix_patterns(urlpatterns)
