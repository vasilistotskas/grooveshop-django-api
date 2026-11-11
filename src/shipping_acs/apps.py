from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ShippingAcsConfig(AppConfig):
    name = "shipping_acs"
    verbose_name = _("ACS Shipping")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Register the ACS adapter with the generic shipping registry.
        # Side-effect-only import: the @register_provider decorator on
        # AcsCarrier runs at import time.
        from shipping_acs import carrier  # noqa: F401
