from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ShippingBoxNowConfig(AppConfig):
    """The BoxNow integration: parcels, lockers, tracking and the webhook.

    Models and entry points: ``docs/app-map.md``. Read
    ``docs/order/shipping-boxnow.md``.
    """

    name = "shipping_boxnow"
    verbose_name = _("BoxNow Shipping")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Register the BoxNow adapter with the generic shipping
        # registry. Importing the module triggers the
        # @register_provider decorator. Side-effect-only import.
        from shipping_boxnow import carrier  # noqa: F401
