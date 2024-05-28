from django.apps import AppConfig


class NotificationConfig(AppConfig):
    name = "notification"

    def ready(self):
        from . import signals  # noqa
