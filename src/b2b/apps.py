from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class B2BConfig(AppConfig):
    """The wholesale programme: business profiles, VIES validation, customer
    groups and their price lists.

    Models and entry points: ``docs/app-map.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "b2b"
    verbose_name = _("Wholesale")
