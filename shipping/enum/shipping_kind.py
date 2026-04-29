from django.db import models
from django.utils.translation import gettext_lazy as _


class ShippingKind(models.TextChoices):
    """Generic shipping fulfilment kind, independent of provider.

    A provider can opt into one or both kinds via the ``ShippingProvider``
    model's ``supports_home_delivery`` / ``supports_pickup_point`` flags.
    """

    HOME_DELIVERY = "home_delivery", _("Home delivery")
    PICKUP_POINT = "pickup_point", _("Pickup point / locker")
