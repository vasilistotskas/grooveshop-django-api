from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class MetaCapiConfig(AppConfig):
    """Meta Conversions API: receivers on order and registration events and the
    tasks that post them.

    Models and entry points: ``docs/app-map.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "meta_capi"
    verbose_name = _("Meta Conversions API")

    def ready(self) -> None:
        from . import signals  # noqa: F401  — register receivers
