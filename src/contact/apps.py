from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ContactConfig(AppConfig):
    """Contact enquiries with scanned attachments, and feedback, with their
    retention sweeps.

    Models and entry points: ``docs/app-map.md``.
    """

    name = "contact"
    verbose_name = _("Contact")

    def ready(self):
        from . import signals  # noqa
