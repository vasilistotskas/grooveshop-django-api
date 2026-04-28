from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ShippingBoxNowConfig(AppConfig):
    name = "shipping_boxnow"
    verbose_name = _("BoxNow Shipping")
    default_auto_field = "django.db.models.BigAutoField"
