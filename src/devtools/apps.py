from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class DevtoolsConfig(AppConfig):
    """Test factories' base class, the demo store's data, the staging
    anonymiser and the seed commands. No models.

    Models and entry points: ``docs/app-map.md``. Read ``docs/testing.md``
    and ``docs/staging-anonymise.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "devtools"
    verbose_name = _("Developer Tools")
