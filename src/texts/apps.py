from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TextsConfig(AppConfig):
    """Store-edited storefront wording and the store admin's text editor.

    Models and entry points: ``docs/app-map.md``. Read
    ``docs/store-texts.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "texts"
    verbose_name = _("Storefront texts")
