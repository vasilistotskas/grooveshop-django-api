from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class B2BConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "b2b"
    verbose_name = _("Wholesale")
