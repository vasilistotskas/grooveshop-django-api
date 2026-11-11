from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class VatConfig(AppConfig):
    """VAT rates.

    Models and entry points: ``docs/app-map.md``.
    """

    name = "vat"
    verbose_name = _("VAT")
