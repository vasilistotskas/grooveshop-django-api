from django.db import models
from django.utils.translation import gettext_lazy as _


class PaymentTransactionKind(models.TextChoices):
    """What a ``PaymentTransaction`` row says happened to the money.

    Only kinds something writes. A chargeback kind belongs with the dispute
    handlers that would write it (a dispute is not a loss until it closes
    against the merchant); it is added with them, not before.
    """

    CHARGE = "CHARGE", _("Charge")
    REFUND = "REFUND", _("Refund")
    COD_COLLECTION = "COD_COLLECTION", _("Cash on delivery collection")
