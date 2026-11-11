from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BuilderConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "builder"
    verbose_name = _("Page builder")

    def ready(self) -> None:
        from builder import data_purge

        data_purge.connect()
