from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class IndexNowConfig(AppConfig):
    """IndexNow: queues a published product or blog post that changed and sends
    one batch per store to search engines.

    Models and entry points: ``docs/app-map.md``. Read ``docs/indexnow.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "indexnow"
    verbose_name = _("IndexNow")

    def ready(self) -> None:
        from . import signals  # noqa: F401  — register receivers
