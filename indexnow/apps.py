from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class IndexNowConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "indexnow"
    verbose_name = _("IndexNow")

    def ready(self) -> None:
        from . import signals  # noqa: F401  — register receivers
