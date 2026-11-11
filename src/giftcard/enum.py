from django.db import models
from django.utils.translation import gettext_lazy as _


class GiftCardStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("Active")
    DISABLED = "DISABLED", _("Disabled")


class GiftCardSource(models.TextChoices):
    ADMIN = "ADMIN", _("Issued by admin")
    PURCHASE = "PURCHASE", _("Purchased in store")


class GiftCardTransactionKind(models.TextChoices):
    """Signed ledger kinds: ISSUE / REFUND_CREDIT / positive ADJUST add
    balance; REDEEM / EXPIRE / negative ADJUST remove it."""

    ISSUE = "ISSUE", _("Issue")
    REDEEM = "REDEEM", _("Redeem")
    REFUND_CREDIT = "REFUND_CREDIT", _("Refund credit")
    ADJUST = "ADJUST", _("Adjust")
    EXPIRE = "EXPIRE", _("Expire")


class GiftCardPurchaseStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending payment")
    PAID = "PAID", _("Paid")
    FAILED = "FAILED", _("Failed")
    CANCELED = "CANCELED", _("Canceled")
