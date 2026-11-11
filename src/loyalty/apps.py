from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LoyaltyConfig(AppConfig):
    """Loyalty points and tiers: awarding, reversing and expiring.

    Models and entry points: ``docs/app-map.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "loyalty"
    verbose_name = _("Loyalty")

    def ready(self):
        import loyalty.signals  # noqa: F401
