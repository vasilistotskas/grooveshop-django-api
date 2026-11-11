from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class DevtoolsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "devtools"
    verbose_name = _("Developer Tools")
