from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TextsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "texts"
    verbose_name = _("Storefront texts")
