from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class VatConfig(AppConfig):
    name = "vat"
    verbose_name = _("VAT")
