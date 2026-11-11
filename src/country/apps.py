from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CountryConfig(AppConfig):
    """Countries: reference rows in the public schema, shared by every store.

    Models and entry points: ``docs/app-map.md``.
    """

    name = "country"
    verbose_name = _("Countries")
