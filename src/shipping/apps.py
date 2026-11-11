from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ShippingConfig(AppConfig):
    name = "shipping"
    verbose_name = _("Shipping")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Side-effect-only import: the @register_provider decorator on
        # FlatRateCarrier puts it in the registry. Registered HERE, by
        # the shipping app itself rather than by a carrier app, because
        # it is the platform's own fallback — a deployment that somehow
        # omitted it would leave every store without a carrier contract
        # unable to take an order.
        from shipping.carriers import flat_rate  # noqa: F401
