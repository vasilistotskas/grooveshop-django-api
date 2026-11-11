from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class UserConfig(AppConfig):
    """User accounts (platform staff in the public schema, customers in each
    store's), addresses, subscriptions and data exports.

    Models and entry points: ``docs/app-map.md``.
    """

    name = "user"
    verbose_name = _("Users")

    def ready(self):
        from . import signals  # noqa
