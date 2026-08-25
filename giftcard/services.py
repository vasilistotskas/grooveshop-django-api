"""Gift-card issuing, redemption and purchase completion.

Mirrors ``LoyaltyService`` mechanics: row locks + an append-only
ledger + at-most-one REDEEM per (card, order). Redemption happens at
ORDER CREATION only (no cart-stage reservation state machine) and is
applied LAST in the value flow — after promotions and loyalty — because
it is payment, not discount.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money
from extra_settings.models import Setting

from giftcard.enum import (
    GiftCardPurchaseStatus,
    GiftCardSource,
    GiftCardStatus,
    GiftCardTransactionKind,
)
from giftcard.models import GiftCard, GiftCardPurchase, GiftCardTransaction

logger = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
MAX_CARDS_PER_ORDER = 3

# Stripe's minimum charge for EUR. When gift cards cover all but less
# than this, the redemption is trimmed so the provider still has a
# chargeable remainder (the sliver stays on the card).
MIN_PROVIDER_CHARGE = Decimal("0.50")


class GiftCardError(ValidationError):
    """A gift-card operation was refused; ``reason`` is machine-readable."""

    def __init__(self, reason: str, message):
        self.reason = reason
        super().__init__(str(message))


@dataclass
class RedemptionPlan:
    """What a set of codes can contribute against an amount due."""

    amount: Money
    per_card: list[tuple[GiftCard, Decimal]]


class GiftCardService:
    @classmethod
    def is_enabled(cls) -> bool:
        return bool(Setting.get("GIFT_CARDS_ENABLED", default=False))

    # ── issuing ────────────────────────────────────────────────────

    @classmethod
    def generate_code(cls) -> str:
        while True:
            groups = [
                "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
                for _ in range(3)
            ]
            code = f"GC-{groups[0]}-{groups[1]}-{groups[2]}"
            if not GiftCard.objects.filter(code=code).exists():
                return code

    @classmethod
    def default_expiry(cls):
        days = int(Setting.get("GIFT_CARD_VALIDITY_DAYS", default=1825) or 0)
        if days <= 0:
            return None
        return timezone.now() + timedelta(days=days)

    @classmethod
    def issue(
        cls,
        amount: Money,
        *,
        source: str = GiftCardSource.ADMIN,
        issued_to=None,
        recipient_email: str = "",
        recipient_name: str = "",
        sender_name: str = "",
        message: str = "",
        deliver_at=None,
        expires_at=None,
        purchase: GiftCardPurchase | None = None,
        created_by=None,
        description: str = "",
    ) -> GiftCard:
        if amount.amount <= 0:
            raise GiftCardError(
                "gift_card_invalid_amount",
                _("Gift card value must be positive."),
            )
        card = GiftCard.objects.create(
            code=cls.generate_code(),
            initial_value=amount,
            source=source,
            issued_to=issued_to,
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            sender_name=sender_name,
            message=message,
            deliver_at=deliver_at,
            expires_at=(
                expires_at if expires_at is not None else cls.default_expiry()
            ),
            purchase=purchase,
        )
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.ISSUE,
            amount=Decimal(amount.amount),
            created_by=created_by,
            description=description or "Issued",
        )
        return card

    # ── redemption ─────────────────────────────────────────────────

    @classmethod
    def _load_cards(cls, codes, *, lock: bool):
        normalized = [(code or "").strip().upper() for code in codes if code]
        normalized = [code for code in normalized if code]
        if not normalized:
            return []
        if len(normalized) > MAX_CARDS_PER_ORDER:
            raise GiftCardError(
                "gift_card_too_many",
                _("At most %(max)d gift cards per order.")
                % {"max": MAX_CARDS_PER_ORDER},
            )
        queryset = GiftCard.objects.filter(code__in=normalized).order_by("pk")
        if lock:
            queryset = queryset.select_for_update()
        cards = {card.code: card for card in queryset}
        missing = [code for code in normalized if code not in cards]
        if missing:
            raise GiftCardError(
                "gift_card_invalid", _("Unknown gift card code.")
            )
        return [cards[code] for code in dict.fromkeys(normalized)]

    @classmethod
    def plan_redemption(
        cls,
        codes,
        amount_due: Money,
        *,
        lock: bool = False,
    ) -> RedemptionPlan:
        """Compute how much the given cards can settle of ``amount_due``.

        Smallest balance first (drains near-empty cards), respecting
        the provider minimum-charge floor on partial coverage. The
        SAME math runs at payment-intent creation, at the order-create
        verification and at the actual redemption — parity or
        ``PaymentAmountMismatchError``.
        """
        currency = amount_due.currency
        empty = RedemptionPlan(Money(0, currency), [])
        if not codes:
            return empty
        if not cls.is_enabled():
            raise GiftCardError(
                "gift_card_invalid", _("Gift cards are not available.")
            )

        cards = cls._load_cards(codes, lock=lock)
        if not cards:
            return empty

        for card in cards:
            if not card.is_redeemable:
                raise GiftCardError(
                    "gift_card_not_redeemable",
                    _(
                        "Gift card %(code)s is not redeemable "
                        "(inactive, expired or empty)."
                    )
                    % {"code": card.code},
                )

        due = Decimal(amount_due.amount)
        if due <= 0:
            return empty

        ordered = sorted(cards, key=lambda card: card.balance.amount)
        per_card: list[tuple[GiftCard, Decimal]] = []
        remaining = due
        for card in ordered:
            if remaining <= 0:
                break
            take = min(Decimal(card.balance.amount), remaining)
            if take <= 0:
                continue
            per_card.append((card, take))
            remaining -= take

        planned = due - remaining
        if 0 < remaining < MIN_PROVIDER_CHARGE:
            # Trim the last card's contribution so the provider still
            # has a chargeable remainder; the sliver stays on the card.
            trim = MIN_PROVIDER_CHARGE - remaining
            card, take = per_card[-1]
            new_take = take - trim
            if new_take <= 0:
                per_card.pop()
            else:
                per_card[-1] = (card, new_take)
            planned -= trim

        return RedemptionPlan(
            Money(max(planned, Decimal("0")), currency), per_card
        )

    @classmethod
    def record_plan(cls, plan: RedemptionPlan, order) -> Money:
        """Write the REDEEM rows for a plan built with ``lock=True``
        inside the SAME transaction. Returns the settled total for
        ``order.gift_card_amount``."""
        for card, take in plan.per_card:
            GiftCardTransaction.objects.create(
                gift_card=card,
                kind=GiftCardTransactionKind.REDEEM,
                amount=-take,
                order=order,
                description=f"Order #{order.id}",
            )
        if plan.per_card:
            order.metadata["gift_cards"] = [
                {"code": card.code, "amount": str(take)}
                for card, take in plan.per_card
            ]
        return plan.amount

    @classmethod
    def redeem(cls, codes, order, amount_due: Money) -> Money:
        """Plan (locked) + record in one step — the order-first path."""
        plan = cls.plan_redemption(codes, amount_due, lock=True)
        return cls.record_plan(plan, order)

    # ── purchase flow ──────────────────────────────────────────────

    @classmethod
    def validate_purchase_amount(cls, amount: Decimal) -> None:
        minimum = Decimal(str(Setting.get("GIFT_CARD_MIN_AMOUNT", default=10)))
        maximum = Decimal(str(Setting.get("GIFT_CARD_MAX_AMOUNT", default=500)))
        if not (minimum <= amount <= maximum):
            raise GiftCardError(
                "gift_card_invalid_amount",
                _("Gift card amount must be between %(min)s and %(max)s EUR.")
                % {"min": minimum, "max": maximum},
            )

    @classmethod
    def complete_purchase(
        cls, purchase: GiftCardPurchase, payment_id: str = ""
    ) -> GiftCard | None:
        """Webhook-driven: mint + schedule delivery once payment lands.

        Idempotent via the status guard — webhook retries and the
        duplicate Stripe event classes both funnel through here.
        """
        if purchase.status == GiftCardPurchaseStatus.PAID:
            return purchase.gift_cards.first()

        purchase.status = GiftCardPurchaseStatus.PAID
        if payment_id:
            purchase.payment_id = payment_id
        purchase.save(update_fields=["status", "payment_id"])

        card = cls.issue(
            purchase.amount,
            source=GiftCardSource.PURCHASE,
            issued_to=purchase.buyer,
            recipient_email=purchase.recipient_email,
            recipient_name=purchase.recipient_name,
            sender_name=purchase.sender_name,
            message=purchase.message,
            deliver_at=purchase.deliver_at,
            purchase=purchase,
            description=f"Purchase {purchase.uuid}",
        )

        from django.db import connection, transaction

        from giftcard.tasks import (
            deliver_gift_card_email,
            send_gift_card_purchase_receipt,
        )

        schema = connection.schema_name
        purchase_id = purchase.pk
        transaction.on_commit(
            lambda: send_gift_card_purchase_receipt.apply_async(
                args=[purchase_id], headers={"_schema_name": schema}
            )
        )
        if not purchase.deliver_at or purchase.deliver_at <= timezone.now():
            transaction.on_commit(
                lambda: deliver_gift_card_email.apply_async(
                    args=[card.id], headers={"_schema_name": schema}
                )
            )
        return card

    @classmethod
    def handle_purchase_reversal(cls, purchase) -> str:
        """React to a provider-side reversal of a purchase payment.

        Returns ``"processed"`` / ``"skipped"`` (the webhook's
        ``VivaWebhookEvent`` outcome vocabulary). A pending purchase is
        simply cancelled; a completed one voids the issued card when it
        is still untouched, and screams for the ops team when the
        balance has already been spent — clawing back spent value is a
        human decision, not webhook logic.
        """
        if purchase.status == GiftCardPurchaseStatus.PENDING:
            purchase.status = GiftCardPurchaseStatus.CANCELED
            purchase.save(update_fields=["status"])
            return "processed"

        if purchase.status != GiftCardPurchaseStatus.PAID:
            return "skipped"

        purchase.status = GiftCardPurchaseStatus.CANCELED
        purchase.save(update_fields=["status"])
        outcome = "processed"
        for card in purchase.gift_cards.all():
            balance = Decimal(card.balance.amount)
            initial = Decimal(card.initial_value.amount)
            if balance >= initial:
                # Untouched card — void it: zero the ledger and
                # disable, keeping the audit trail append-only.
                if balance > 0:
                    GiftCardTransaction.objects.create(
                        gift_card=card,
                        kind=GiftCardTransactionKind.ADJUST,
                        amount=-balance,
                        description=(
                            f"Voided — payment reversed "
                            f"(purchase {purchase.uuid})"
                        ),
                    )
                card.status = GiftCardStatus.DISABLED
                card.save(update_fields=["status"])
                logger.warning(
                    "Gift card %s voided after payment reversal of purchase %s",
                    card.code,
                    purchase.uuid,
                )
            else:
                logger.error(
                    "Payment for gift card purchase %s was REVERSED but "
                    "card %s already spent %s of %s — manual ops "
                    "intervention required",
                    purchase.uuid,
                    card.code,
                    initial - balance,
                    initial,
                )
        return outcome

    # ── refunds ────────────────────────────────────────────────────

    @classmethod
    def credit_refund(cls, order) -> Decimal:
        """Return the gift-card-settled portion of a refunded order to
        the source card(s). Idempotent per order via the description
        marker check."""
        redeems = GiftCardTransaction.objects.filter(
            order=order, kind=GiftCardTransactionKind.REDEEM
        ).select_related("gift_card")
        already = set(
            GiftCardTransaction.objects.filter(
                order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
            ).values_list("gift_card_id", flat=True)
        )
        credited = Decimal("0")
        for redeem in redeems:
            if redeem.gift_card_id in already:
                continue
            GiftCardTransaction.objects.create(
                gift_card=redeem.gift_card,
                kind=GiftCardTransactionKind.REFUND_CREDIT,
                amount=-redeem.amount,  # redeem rows are negative
                order=order,
                description=f"Refund of order #{order.id}",
            )
            credited += -redeem.amount
        return credited

    # ── expiry ─────────────────────────────────────────────────────

    @classmethod
    def expire_cards(cls) -> int:
        """Write EXPIRE rows for expired cards with a positive balance.

        The negative row equals the remaining balance (never more —
        the loyalty min-clamp rule), so an expired card sums to zero.
        """
        now = timezone.now()
        expired = GiftCard.objects.filter(
            status=GiftCardStatus.ACTIVE,
            expires_at__isnull=False,
            expires_at__lte=now,
        )
        count = 0
        for card in expired:
            balance = Decimal(card.balance.amount)
            if balance <= 0:
                continue
            GiftCardTransaction.objects.create(
                gift_card=card,
                kind=GiftCardTransactionKind.EXPIRE,
                amount=-balance,
                description="Expired",
            )
            count += 1
        if count:
            logger.info("Expired %d gift cards", count)
        return count

    # ── lookups ────────────────────────────────────────────────────

    @classmethod
    def check(cls, code: str) -> GiftCard:
        if not cls.is_enabled():
            raise GiftCardError(
                "gift_card_invalid", _("Gift cards are not available.")
            )
        try:
            return GiftCard.objects.get(code=(code or "").strip().upper())
        except GiftCard.DoesNotExist as exc:
            raise GiftCardError(
                "gift_card_invalid", _("Unknown gift card code.")
            ) from exc

    @classmethod
    def default_currency(cls) -> str:
        return settings.DEFAULT_CURRENCY
