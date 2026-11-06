from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SchedulerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "scheduler"
    verbose_name = _("Background Jobs")
