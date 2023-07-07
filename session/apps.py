from django.apps import AppConfig
from django.contrib.auth import user_logged_in
from django.contrib.auth import user_logged_out


class SessionConfig(AppConfig):
    name = "session"

    def ready(self):
        from . import signals

        user_logged_in.connect(signals.update_session_user_log_in)
        user_logged_out.connect(signals.update_session_user_log_out)
