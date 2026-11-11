from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class NotificationConfig(AppConfig):
    """In-app notifications and the WebSocket consumer that delivers them.

    Models and entry points: ``docs/app-map.md``.
    """

    name = "notification"
    verbose_name = _("Notifications")

    def ready(self):
        from . import signals  # noqa
