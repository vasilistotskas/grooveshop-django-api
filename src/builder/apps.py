from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BuilderConfig(AppConfig):
    """The storefront page builder: versioned documents, immutable releases,
    the staff API, the public read and the media library.

    Models and entry points: ``docs/app-map.md``. Read ``docs/builder.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "builder"
    verbose_name = _("Page builder")

    def ready(self) -> None:
        from builder import data_purge

        data_purge.connect()
