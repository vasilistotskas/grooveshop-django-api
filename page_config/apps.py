from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PageConfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "page_config"
    verbose_name = _("Page Configuration")
