from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class MetaCapiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "meta_capi"
    verbose_name = _("Meta Conversions API")

    def ready(self) -> None:
        from . import signals  # noqa: F401  — register receivers
