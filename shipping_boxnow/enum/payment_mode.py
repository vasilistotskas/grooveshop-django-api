from django.db import models
from django.utils.translation import gettext_lazy as _


class BoxNowPaymentMode(models.TextChoices):
    PREPAID = "prepaid", _("Prepaid")
    COD = "cod", _("Cash on delivery")
