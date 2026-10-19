from django.db import models
from django.utils.translation import gettext_lazy as _


class PaymentTransactionKind(models.TextChoices):
    """What a ``PaymentTransaction`` row says happened to the money.

    Only kinds something writes. A CHARGEBACK is written when a dispute
    closes against the merchant, never when it opens: until then the money
    is still ours to defend. A REFUND_REVERSAL is written when a provider
    reports an accepted refund failed (Stripe ``refund.failed``).
    """

    CHARGE = "CHARGE", _("Charge")
    REFUND = "REFUND", _("Refund")
    CHARGEBACK = "CHARGEBACK", _("Chargeback")
    REFUND_REVERSAL = "REFUND_REVERSAL", _("Refund reversal")
    COD_COLLECTION = "COD_COLLECTION", _("Cash on delivery collection")
    OFFLINE_COLLECTION = "OFFLINE", _("Collected off-platform")


#: The kinds that move money against one CHARGE and so name it. A
#: REFUND_REVERSAL is the one that points the other way: a refund the
#: provider reported failed after the fact, so that money was never paid
#: back and counts as still held.
CHARGE_REVERSAL_KINDS = (
    PaymentTransactionKind.REFUND,
    PaymentTransactionKind.CHARGEBACK,
    PaymentTransactionKind.REFUND_REVERSAL,
)
