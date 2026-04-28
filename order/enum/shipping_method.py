from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderShippingMethod(models.TextChoices):
    HOME_DELIVERY = "home_delivery", _("Home delivery")
    BOX_NOW_LOCKER = "box_now_locker", _("BOX NOW Locker")
