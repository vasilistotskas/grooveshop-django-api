from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class NotificationConfig(AppConfig):
    name = "notification"
    verbose_name = _("Notifications")

    def ready(self):
        from . import signals  # noqa
