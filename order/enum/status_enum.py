from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderStatusEnum(models.TextChoices):
    SENT = "SENT", _("Sent")
    PAID_AND_SENT = "PAID_AND_SENT", _("Paid and Sent")
    CANCELED = "CANCELED", _("Canceled")
    PENDING = "PENDING", _("Pending")
