from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ShippingConfig(AppConfig):
    name = "shipping"
    verbose_name = _("Shipping")
    default_auto_field = "django.db.models.BigAutoField"
