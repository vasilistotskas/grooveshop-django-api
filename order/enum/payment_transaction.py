from django.db import models
from django.utils.translation import gettext_lazy as _


class PaymentTransactionKind(models.TextChoices):
    """What a ``PaymentTransaction`` row says happened to the money.

    Only kinds something writes. A CHARGEBACK is written when a dispute
    closes against the merchant, never when it opens: until then the money
    is still ours to defend.
    """

    CHARGE = "CHARGE", _("Charge")
    REFUND = "REFUND", _("Refund")
    CHARGEBACK = "CHARGEBACK", _("Chargeback")
    COD_COLLECTION = "COD_COLLECTION", _("Cash on delivery collection")


#: The kinds that take money back out of one CHARGE and so name it.
CHARGE_REVERSAL_KINDS = (
    PaymentTransactionKind.REFUND,
    PaymentTransactionKind.CHARGEBACK,
)
