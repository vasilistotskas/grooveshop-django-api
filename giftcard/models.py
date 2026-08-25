"""Gift cards: stored-value payment instruments.

A gift card is NOT a discount — it is an accounting liability with an
append-only transaction ledger (the loyalty ``PointsTransaction``
pattern). Balance is ALWAYS derived by summing the ledger; there is no
cached balance column to drift. Redemption reduces what the payment
provider charges, never the taxable order value (multi-purpose voucher:
VAT is due at redemption on the goods, the card sale itself is not a
goods sale and is never submitted to myDATA).
"""

from decimal import Decimal

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money

from core.models import TimeStampMixinModel, UUIDModel
from giftcard.enum import (
    GiftCardPurchaseStatus,
    GiftCardSource,
    GiftCardStatus,
    GiftCardTransactionKind,
)


class GiftCardPurchase(TimeStampMixinModel, UUIDModel):
    """A storefront gift-card purchase awaiting / holding payment.

    Deliberately NOT an ``Order`` — orders drag stock, shipping,
    courier dispatch and the AADE retail-receipt pipeline behind them,
    all wrong for a multi-purpose voucher sale.
    """

    id = models.BigAutoField(primary_key=True)
    buyer = models.ForeignKey(
        "user.UserAccount",
        related_name="gift_card_purchases",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    buyer_email = models.EmailField(_("Buyer Email"))
    amount = MoneyField(_("Amount"), max_digits=11, decimal_places=2)
    recipient_email = models.EmailField(_("Recipient Email"))
    recipient_name = models.CharField(
        _("Recipient Name"), max_length=255, blank=True, default=""
    )
    sender_name = models.CharField(
        _("Sender Name"), max_length=255, blank=True, default=""
    )
    message = models.TextField(_("Message"), blank=True, default="")
    deliver_at = models.DateTimeField(
        _("Deliver At"),
        null=True,
        blank=True,
        help_text=_("Empty means deliver immediately after payment"),
    )
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=GiftCardPurchaseStatus,
        default=GiftCardPurchaseStatus.PENDING,
    )
    provider_code = models.CharField(
        _("Provider Code"), max_length=50, blank=True, default=""
    )
    payment_id = models.CharField(
        _("Payment ID"), max_length=255, blank=True, default=""
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Gift Card Purchase")
        verbose_name_plural = _("Gift Card Purchases")
        ordering = ["-created_at"]
        db_table = "giftcard_purchase"
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["status"], name="giftcard_purchase_status_ix"),
            BTreeIndex(
                fields=["payment_id"], name="giftcard_purchase_payment_ix"
            ),
        ]

    def __str__(self):
        return f"Gift card purchase {self.amount} → {self.recipient_email}"


class GiftCard(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(
        _("Code"),
        max_length=32,
        unique=True,
        help_text=_("Crypto-random, uppercased; the bearer secret"),
    )
    initial_value = MoneyField(
        _("Initial Value"), max_digits=11, decimal_places=2
    )
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=GiftCardStatus,
        default=GiftCardStatus.ACTIVE,
    )
    expires_at = models.DateTimeField(
        _("Expires At"),
        null=True,
        blank=True,
        help_text=_(
            "Defaults to GIFT_CARD_VALIDITY_DAYS after issue (Greek law: "
            "5 years); empty means never expires"
        ),
    )
    source = models.CharField(
        _("Source"),
        max_length=10,
        choices=GiftCardSource,
        default=GiftCardSource.ADMIN,
    )
    issued_to = models.ForeignKey(
        "user.UserAccount",
        related_name="gift_cards",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=_(
            "Optional account link — lets the shopper see the card "
            "under 'My gift cards'. Redemption only needs the code."
        ),
    )
    recipient_email = models.EmailField(
        _("Recipient Email"), blank=True, default=""
    )
    recipient_name = models.CharField(
        _("Recipient Name"), max_length=255, blank=True, default=""
    )
    sender_name = models.CharField(
        _("Sender Name"), max_length=255, blank=True, default=""
    )
    message = models.TextField(_("Message"), blank=True, default="")
    deliver_at = models.DateTimeField(_("Deliver At"), null=True, blank=True)
    delivered_at = models.DateTimeField(
        _("Delivered At"), null=True, blank=True
    )
    expiry_reminder_sent_at = models.DateTimeField(
        _("Expiry Reminder Sent At"),
        null=True,
        blank=True,
        help_text=_(
            "Stamped by the daily reminder sweep so each card is "
            "reminded exactly once"
        ),
    )
    purchase = models.ForeignKey(
        GiftCardPurchase,
        related_name="gift_cards",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Gift Card")
        verbose_name_plural = _("Gift Cards")
        ordering = ["-created_at"]
        db_table = "giftcard"
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["status"], name="giftcard_status_ix"),
            BTreeIndex(fields=["issued_to"], name="giftcard_issued_to_ix"),
            BTreeIndex(fields=["expires_at"], name="giftcard_expires_ix"),
            BTreeIndex(
                fields=["recipient_email"], name="giftcard_recipient_ix"
            ),
        ]

    def __str__(self):
        return f"{self.code} ({self.balance})"

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    @property
    def balance(self) -> Money:
        """Derived: the signed sum of the ledger, floored at zero."""
        total = self.transactions.aggregate(total=Sum("amount"))[
            "total"
        ] or Decimal("0")
        currency = self.initial_value.currency
        return Money(max(total, Decimal("0")), currency)

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def is_redeemable(self) -> bool:
        return (
            self.status == GiftCardStatus.ACTIVE
            and not self.is_expired
            and self.balance.amount > 0
        )


class GiftCardTransaction(TimeStampMixinModel):
    """Append-only ledger row. Never update or delete — corrections
    are new ADJUST rows, exactly like loyalty's PointsTransaction."""

    id = models.BigAutoField(primary_key=True)
    gift_card = models.ForeignKey(
        GiftCard,
        related_name="transactions",
        on_delete=models.PROTECT,
    )
    kind = models.CharField(
        _("Kind"),
        max_length=15,
        choices=GiftCardTransactionKind,
    )
    amount = models.DecimalField(
        _("Amount"),
        max_digits=11,
        decimal_places=2,
        help_text=_(
            "Signed: positive adds balance (issue/refund credit), "
            "negative removes it (redeem/expire)"
        ),
    )
    order = models.ForeignKey(
        "order.Order",
        related_name="gift_card_transactions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_by = models.ForeignKey(
        "user.UserAccount",
        related_name="gift_card_adjustments_made",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=_("Admin who made a manual adjustment"),
    )
    description = models.CharField(
        _("Description"), max_length=255, blank=True, default=""
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Gift Card Transaction")
        verbose_name_plural = _("Gift Card Transactions")
        ordering = ["-created_at"]
        db_table = "giftcard_transaction"
        constraints = [
            # One redemption per card per order — the idempotency
            # anchor for retried order-create transactions.
            models.UniqueConstraint(
                fields=["gift_card", "order"],
                condition=models.Q(kind="REDEEM"),
                name="unique_giftcard_redeem_per_order",
            ),
        ]
        indexes = [
            # Explicit names: the mixin's %(class)s naming exceeds the
            # 32-char index-name cap for this class name (models.E034).
            BTreeIndex(fields=["created_at"], name="giftcard_tx_created_ix"),
            BTreeIndex(fields=["updated_at"], name="giftcard_tx_updated_ix"),
            BTreeIndex(fields=["gift_card"], name="giftcard_tx_card_ix"),
            BTreeIndex(fields=["kind"], name="giftcard_tx_kind_ix"),
            BTreeIndex(fields=["order"], name="giftcard_tx_order_ix"),
        ]

    def __str__(self):
        return f"{self.kind} {self.amount} on {self.gift_card_id}"
