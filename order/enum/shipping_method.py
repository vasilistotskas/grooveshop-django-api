from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderShippingMethod(models.TextChoices):
    """Customer-facing shipping option keys.

    Legacy enum kept alongside the (shipping_provider, shipping_kind)
    pair so older API clients and admin dashboards continue to work.
    The new code paths read the pair preferentially — see
    OrderService._resolve_shipping_provider.
    """

    HOME_DELIVERY = "home_delivery", _("Home delivery")
    BOX_NOW_LOCKER = "box_now_locker", _("BOX NOW Locker")
    ACS_SMARTPOINT = "acs_smartpoint", _("ACS Smartpoint")
