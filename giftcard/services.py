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
from django.db.models import DurationField, F
from django.db.models.expressions import RawSQL
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
from giftcard.models import (
    GiftCard,
    GiftCardPause,
    GiftCardPurchase,
    GiftCardTransaction,
)
from tenant.celery import dispatch_on_commit

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
        """Plan AND merchant toggle — both must hold.

        The plan half is checked HERE rather than only on the gift-card
        endpoints because order create runs with no permission classes
        (public action, for guest checkout), so redemption bypassed the
        endpoint gate entirely. See tenant.membership.tenant_plan_allows.
        """
        from tenant.membership import tenant_plan_allows

        if not tenant_plan_allows("gift_cards_enabled"):
            return False
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
            # A card's balance is denominated in its OWN currency, and
            # the plan below compares raw Decimals. Without this, a $100
            # card settled a EUR 100 order one-for-one and extinguished
            # EUR 100 of liability against roughly EUR 92 of instrument.
            # There is no FX rate anywhere in this codebase to convert
            # with, and inventing 1:1 silently is the worst of the
            # options — so refuse, and say which card.
            if card.balance.currency != currency:
                raise GiftCardError(
                    "gift_card_currency_mismatch",
                    _(
                        "Gift card %(code)s is in %(card_currency)s and "
                        "cannot pay for a %(order_currency)s order."
                    )
                    % {
                        "code": card.code,
                        "card_currency": card.balance.currency,
                        "order_currency": currency,
                    },
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

        # Respect the provider minimum-charge floor on partial
        # coverage: trim the redemption so the provider remainder is
        # never a sub-minimum sliver (it stays on the card instead).
        # Popping a fully-trimmed card changes the remainder again, so
        # iterate; ``per_card`` is the single source of truth and the
        # planned amount is always its sum — the ledger rows written
        # by ``record_plan`` and ``order.gift_card_amount`` must agree
        # to the cent.
        while per_card:
            planned = sum(take for _, take in per_card)
            shortfall = due - planned
            if shortfall <= 0 or shortfall >= MIN_PROVIDER_CHARGE:
                break
            trim = MIN_PROVIDER_CHARGE - shortfall
            card, take = per_card[-1]
            if take - trim <= 0:
                per_card.pop()
            else:
                per_card[-1] = (card, take - trim)

        planned = sum((take for _, take in per_card), start=Decimal(0))
        return RedemptionPlan(Money(planned, currency), per_card)

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
        cls,
        purchase: GiftCardPurchase,
        payment_id: str = "",
        transaction_id: str = "",
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
        # Stripe's payment id IS the transaction; Viva's order code is not.
        purchase.provider_transaction_id = transaction_id or payment_id
        purchase.save(
            update_fields=["status", "payment_id", "provider_transaction_id"]
        )

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

        from giftcard.tasks import (
            deliver_gift_card_email,
            send_gift_card_purchase_receipt,
        )

        purchase_id = purchase.pk
        dispatch_on_commit(send_gift_card_purchase_receipt, [purchase_id])
        if not purchase.deliver_at or purchase.deliver_at <= timezone.now():
            dispatch_on_commit(deliver_gift_card_email, [card.id])
        return card

    @classmethod
    def handle_purchase_reversal(cls, purchase) -> str:
        """React to the provider taking a purchase's payment back: a refund
        that reaches the purchase amount, a refund claim, a chargeback.

        Returns ``"processed"`` / ``"skipped"`` (the webhook's
        ``VivaWebhookEvent`` outcome vocabulary). A pending purchase is
        simply cancelled. A completed one voids what is still on every
        card it issued, whether or not the card was touched, so the money
        the provider took back can never be spent. What was already spent
        is gone for good: it is recorded on the purchase
        (``unrecovered_amount``), logged as an error and mailed to staff,
        because clawing back spent value is a human decision.
        """
        if purchase.status == GiftCardPurchaseStatus.PENDING:
            purchase.status = GiftCardPurchaseStatus.CANCELED
            purchase.save(update_fields=["status"])
            return "processed"

        if purchase.status != GiftCardPurchaseStatus.PAID:
            return "skipped"

        purchase.status = GiftCardPurchaseStatus.CANCELED
        unrecovered = Decimal(0)
        for card in purchase.gift_cards.all():
            balance = Decimal(card.balance.amount)
            initial = Decimal(card.initial_value.amount)
            if balance > 0:
                # Void what is left: zero the ledger and disable, keeping
                # the audit trail append-only.
                GiftCardTransaction.objects.create(
                    gift_card=card,
                    kind=GiftCardTransactionKind.ADJUST,
                    amount=-balance,
                    description=(
                        f"Voided — payment reversed (purchase {purchase.uuid})"
                    ),
                )
            card.status = GiftCardStatus.DISABLED
            card.save(update_fields=["status"])
            logger.warning(
                "Gift card %s voided after payment reversal of purchase %s",
                card.code,
                purchase.uuid,
            )
            if balance < initial:
                unrecovered += initial - balance
        purchase.unrecovered_amount = unrecovered
        purchase.save(update_fields=["status", "unrecovered_amount"])
        if unrecovered > 0:
            cls._alert_unrecovered(purchase, unrecovered)
        return "processed"

    @classmethod
    def _alert_unrecovered(cls, purchase, unrecovered: Decimal) -> None:
        from shipping.alerts import OpsAlertKind, send_ops_alert

        logger.error(
            "Payment for gift card purchase %s was REVERSED but %s of %s was "
            "already spent — manual ops intervention required",
            purchase.uuid,
            unrecovered,
            purchase.amount,
        )
        try:
            send_ops_alert(
                kind=OpsAlertKind.GIFT_CARD_REVERSAL,
                subject="Gift card spent before its payment was reversed",
                message=(
                    f"The payment for gift card purchase {purchase.uuid} "
                    f"({purchase.amount}) was reversed by the provider. The "
                    "unspent balance was voided, but "
                    f"{unrecovered} of the card had already been spent and "
                    "cannot be recovered automatically. Decide how to "
                    "reconcile it (see the purchase in the admin)."
                ),
            )
        except Exception:
            logger.exception(
                "Could not send the gift-card reversal alert for %s",
                purchase.uuid,
            )

    @classmethod
    def alert_purchase_disputed(cls, purchase, dispute_id: str) -> bool:
        """Tell staff the payment of a purchase is in dispute, once per
        dispute. Nothing is voided yet: until the dispute is lost the money
        is still the merchant's to defend.

        Returns whether staff were told: ``False`` for a dispute already
        announced (a redelivered event, a later status of the same
        dispute). The purchase row must be locked by the caller.
        """
        from shipping.alerts import OpsAlertKind, send_ops_alert

        if purchase.dispute_id == dispute_id:
            return False
        purchase.dispute_id = dispute_id
        purchase.save(update_fields=["dispute_id"])

        logger.error(
            "Payment for gift card purchase %s is DISPUTED", purchase.uuid
        )
        try:
            send_ops_alert(
                kind=OpsAlertKind.GIFT_CARD_REVERSAL,
                subject="Gift card payment disputed",
                message=(
                    f"The payment for gift card purchase {purchase.uuid} "
                    f"({purchase.amount}) is being disputed. The card is "
                    "voided automatically if the dispute is lost; consider "
                    "disabling it now."
                ),
            )
        except Exception:
            logger.exception(
                "Could not send the gift-card dispute alert for %s",
                purchase.uuid,
            )
        return True

    # ── refunds ────────────────────────────────────────────────────

    @classmethod
    def credit_refund(
        cls,
        order,
        amount: Decimal | None = None,
        refund_attempt=None,
    ) -> Decimal:
        """Return gift-card value to the cards that gave it for *order*:
        *amount* of it, or everything not back yet when ``amount`` is
        ``None`` (a cancel). Returns what was credited.

        An order is refunded onto its cards in parts (``OrderService.
        refund_order`` pays the provider first, then what the order paid
        with gift cards), so a card can be credited more than once for one
        order. What a card is owed is what it gave (REDEEM) less what has
        come back (REFUND_CREDIT), and it is read UNDER THE CARD'S ROW LOCK
        (cards locked in id order, so two credits cannot deadlock): a second
        credit that raced the first finds nothing owed and credits nothing,
        so the cap at what the card gave holds without a unique constraint.
        *amount* is shared out in proportion to what each card is owed, to
        the cent, the last cent going to the card that can still take it;
        an *amount* that is everything owed returns each card its exact
        remainder. ``refund_attempt`` names the refund that made the credit.
        """
        from django.db import transaction
        from django.db.models import Sum

        with transaction.atomic():
            gave = {
                row["gift_card_id"]: -row["total"]
                for row in GiftCardTransaction.objects.filter(
                    order=order, kind=GiftCardTransactionKind.REDEEM
                )
                .values("gift_card_id")
                .annotate(total=Sum("amount"))
            }
            if not gave:
                return Decimal(0)
            cards = {
                card.pk: card
                for card in GiftCard.objects.select_for_update()
                .filter(pk__in=gave)
                .order_by("pk")
            }
            back = {
                row["gift_card_id"]: row["total"]
                for row in GiftCardTransaction.objects.filter(
                    order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
                )
                .values("gift_card_id")
                .annotate(total=Sum("amount"))
            }
            owed = {
                card_id: gave[card_id] - back.get(card_id, Decimal(0))
                for card_id in sorted(cards)
                if gave[card_id] - back.get(card_id, Decimal(0)) > 0
            }
            total_owed = sum(owed.values(), Decimal(0))
            want = total_owed if amount is None else min(amount, total_owed)
            if want <= 0:
                return Decimal(0)
            shares = cls._share_by_owed(owed, want)
            for card_id, share in shares.items():
                if share <= 0:
                    continue
                GiftCardTransaction.objects.create(
                    gift_card=cards[card_id],
                    kind=GiftCardTransactionKind.REFUND_CREDIT,
                    amount=share,
                    order=order,
                    refund_attempt=refund_attempt,
                    description=f"Refund of order #{order.id}",
                )
                cls._keep_refund_spendable(cards[card_id])
            return sum(shares.values(), Decimal(0))

    @staticmethod
    def _share_by_owed(
        owed: dict[int, Decimal], want: Decimal
    ) -> dict[int, Decimal]:
        """*want* split over the cards in proportion to what each is owed:
        each share rounded down to the cent, the cents left over handed
        out one by one to the cards that can still take them. Everything
        owed is each card's exact remainder."""
        total = sum(owed.values(), Decimal(0))
        if want >= total:
            return dict(owed)
        cent = Decimal("0.01")
        shares = {
            card_id: (want * due / total).quantize(cent, rounding="ROUND_DOWN")
            for card_id, due in owed.items()
        }
        left = want - sum(shares.values(), Decimal(0))
        for card_id, due in owed.items():
            if left <= 0:
                break
            give = min(left, due - shares[card_id])
            shares[card_id] += give
            left -= give
        return shares

    @classmethod
    def _keep_refund_spendable(cls, card) -> None:
        """Give a lapsed card a fresh window when money returns to it.

        A card can expire while its value is sitting on an order that is
        refunded later. The credit then landed on a card that
        `plan_redemption` refuses (`is_redeemable` is False) and that
        `expire_cards` reclaims on its next run — matching ACTIVE,
        past-`expires_at`, balance now positive. The customer's refund
        was destroyed within a day, and the only trace was
        "Expired N gift cards" while the refund task had reported
        success.

        Refunding money and then confiscating it is not a defensible
        outcome under any expiry policy, so the card gets the same
        window a newly issued one gets, counted from now.
        """
        if card.expires_at is None or card.expires_at > timezone.now():
            return
        fresh = cls.default_expiry()
        if fresh is None:
            return
        previous = card.expires_at
        card.expires_at = fresh
        card.save(update_fields=["expires_at"])
        logger.info(
            "Gift card %s had expired at %s; extended to %s so the "
            "refund credited to it stays spendable",
            card.code,
            previous,
            fresh,
        )

    # ── expiry ─────────────────────────────────────────────────────

    @classmethod
    def sync_pause(cls) -> None:
        """Bring the store's record of gift-card pauses in line with its
        switch: open a span while gift cards are off; when they are back
        on, close it and move every active card's expiry forward by it,
        so no card loses validity while it could not be spent.

        Run when the switch changes (``tenant.switches``) and by the
        daily expiry sweep, which also opens a span for a store that was
        already off before spans were recorded.
        """
        now = timezone.now()
        if not cls.is_enabled():
            GiftCardPause.open_at(now)
            return
        span = GiftCardPause.close_at(now)
        if span is None or span.ended_at is None:
            return
        # The elapsed off time as plain seconds: a timedelta reaches
        # PostgreSQL as an interval of whole days, which it adds as calendar
        # days in the session time zone (Europe/Athens), so a span across a
        # DST change would move a card by an hour more or less than the
        # time gift cards were actually off.
        off = RawSQL(
            "make_interval(secs => %s)",
            ((span.ended_at - span.started_at).total_seconds(),),
            output_field=DurationField(),
        )
        GiftCard.objects.filter(
            status=GiftCardStatus.ACTIVE,
            expires_at__gt=span.started_at,
        ).update(expires_at=F("expires_at") + off, updated_at=now)

    @classmethod
    def expire_cards(cls) -> int:
        """Write EXPIRE rows for expired cards with a positive balance.

        The negative row equals the remaining balance (never more —
        the loyalty min-clamp rule), so an expired card sums to zero.

        Nothing expires while gift cards are off, and the time they were
        off does not count (``sync_pause``).
        """
        cls.sync_pause()
        if not cls.is_enabled():
            return 0
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
