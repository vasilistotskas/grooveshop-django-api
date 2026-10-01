from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class GiftcardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "giftcard"
    verbose_name = _("Gift cards")

    def ready(self):
        import giftcard.signals  # noqa: F401
