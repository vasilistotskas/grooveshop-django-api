from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PageConfigConfig(AppConfig):
    """Content pages (policies, FAQ) and the legal documents: the platform's
    texts, their rollout and the version each order accepted.

    Models and entry points: ``docs/app-map.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "page_config"
    verbose_name = _("Page Configuration")
