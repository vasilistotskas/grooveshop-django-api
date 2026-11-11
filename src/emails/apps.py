from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules
from django.utils.translation import gettext_lazy as _


class EmailsConfig(AppConfig):
    """Every email the platform sends: the type registry, ``send_email``, per-
    store switches, opt-outs, the log and the Resend webhook.

    Models and entry points: ``docs/app-map.md``. Read ``docs/emails.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "emails"
    verbose_name = _("Emails")

    def ready(self) -> None:
        """Collect every app's email types, then lock the catalogue.

        An app declares its types in ``<app>/emails.py`` (importing that
        module registers them); the registry then validates the whole set
        once, so a bad type stops the process at boot, not at its first
        send.
        """
        from emails import checks  # noqa: F401  (registers the deploy guard)
        from emails.registry import registry

        autodiscover_modules("emails")
        registry.freeze()
