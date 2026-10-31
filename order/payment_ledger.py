"""The payment ledger: what providers moved for an order, and what follows.

``PaymentTransaction`` rows are the only record of the money received for
an order (a provider's CHARGE, a courier's COD_COLLECTION) and the money
refunded from it and the money a lost dispute took back (a CHARGEBACK).
Everything else is read from them: how much can still be refunded, which
payment a refund goes against, and whether ``Order.payment_status`` is
REFUNDED or PARTIALLY_REFUNDED (:meth:`PaymentLedger.refund_status`, the
one place that decides it, for the in-app refund, the Stripe
``charge.refunded`` webhook and the Viva reversal webhook alike). A
chargeback lowers what is refundable but never moves ``payment_status``:
the money is already gone, it was not paid back.

Every ``record_*`` method must run with the order row locked by the
caller (``select_for_update``): the sums read here and the row written
here have to be one step. Rows are keyed on the provider's own id for the
movement (``unique_payment_transaction_reference``), so a delivery
repeated by the provider records nothing the second time and says so by
returning ``created=False``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple

from django.db import transaction
from django.db.models import (
    Case,
    DecimalField,
    F,
    QuerySet,
    Sum,
    When,
)
from django.db.models.functions import Coalesce
from djmoney.money import Money

from order.enum.payment_transaction import (
    PaymentFeeSource,
    PaymentTransactionKind,
)
from order.enum.status import PaymentStatus
from order.models.payment_transaction import PaymentTransaction
from order.models.pending_payment_fee import PendingPaymentFee
from order.models.refund_attempt import (
    RefundAttempt,
    RefundAttemptKind,
    RefundAttemptStatus,
)
from order.signals import order_charged_back

if TYPE_CHECKING:
    from order.models.order import Order

logger = logging.getLogger(__name__)

_ZERO = Decimal(0)
_AMOUNT = DecimalField(max_digits=11, decimal_places=2)

#: The rows that put money into the ledger and can be paid back out of it.
_RECEIPT_KINDS = (
    PaymentTransactionKind.CHARGE,
    PaymentTransactionKind.COD_COLLECTION,
    PaymentTransactionKind.OFFLINE_COLLECTION,
)

#: The provider code of money a staff member confirmed arrived by bank
#: transfer (``OrderService.confirm_offline_payment``). Not a registered
#: payment provider, so nothing can mistake it for one.
OFFLINE_TRANSFER_PROVIDER = "bank_transfer"

#: Who moved the money of a refund the merchant made themselves, outside any
#: payment provider (a bank transfer, cash handed over). Not a registered
#: provider code, so nothing can mistake it for one.
MANUAL_REFUND_PROVIDER = "manual"

#: The longest "how was it paid back" reference accepted; the ledger prefixes
#: it with the order and the collection it refunds.
REPAYMENT_REFERENCE_MAX_LENGTH = 100

#: What a provider can say about a payment that was captured. Viva reports
#: ``R`` for a payment that was refunded afterwards, which is still a
#: capture; Stripe keeps a refunded PaymentIntent ``succeeded``.
_CAPTURED_STATUSES = frozenset(
    {PaymentStatus.COMPLETED, PaymentStatus.REFUNDED}
)


#: Payment statuses of an order money is still held for, and so one whose
#: capture a refund may have to read from the provider.
_HELD_PAYMENT_STATUSES = frozenset(
    {PaymentStatus.COMPLETED, PaymentStatus.PARTIALLY_REFUNDED}
)


class ChargeBalance(NamedTuple):
    """A payment and how much of it can still be refunded."""

    charge: PaymentTransaction
    refundable: Decimal


class StoredValue(NamedTuple):
    """What an order paid with gift cards and loyalty points, and how much
    of it has gone back. All in the order's currency.

    Read from the gift-card and points ledgers, never from a counter, so a
    credit made by a cancel and one made by a refund add up. ``gift_card_*``
    is what the cards gave for the order (REDEEM) and what came back
    (REFUND_CREDIT); ``points_*`` the points spent on it and the points
    restored, with ``points_value`` the order's loyalty discount that the
    spent points were worth.
    """

    gift_card_total: Decimal
    gift_card_returned: Decimal
    points_spent: int
    points_restored: int
    points_value: Decimal

    @property
    def gift_card_left(self) -> Decimal:
        return max(self.gift_card_total - self.gift_card_returned, _ZERO)

    @property
    def points_left(self) -> int:
        return max(self.points_spent - self.points_restored, 0)

    @property
    def points_value_left(self) -> Decimal:
        if not self.points_spent:
            return _ZERO
        return (
            self.points_value * self.points_left / self.points_spent
        ).quantize(Decimal("0.01"))

    @property
    def left(self) -> Decimal:
        """The value that has not gone back."""
        return self.gift_card_left + self.points_value_left

    @property
    def returned(self) -> Decimal:
        """The value that has."""
        return self.gift_card_returned + (
            self.points_value - self.points_value_left
            if self.points_spent
            else _ZERO
        )


class ChargebackOutcome(NamedTuple):
    """What :meth:`PaymentLedger.record_chargeback` did: the row (``None``
    when nothing was left to take), whether it is new, the amount the
    provider named, the amount recorded (capped at what the payment held)
    and what refunds and chargebacks had already taken from the payment."""

    row: PaymentTransaction | None
    created: bool
    requested: Money
    recorded: Money
    returned: Decimal


def is_cash_collection(row: PaymentTransaction) -> bool:
    """Money no payment provider moved (cash a courier collected, a bank
    transfer a staff member confirmed): there is no provider to refund it
    through, so the merchant pays it back themselves."""
    return row.kind in (
        PaymentTransactionKind.COD_COLLECTION,
        PaymentTransactionKind.OFFLINE_COLLECTION,
    )


def _total(rows: QuerySet[PaymentTransaction]) -> Decimal:
    return rows.aggregate(
        total=Coalesce(Sum("amount", output_field=_AMOUNT), _ZERO)
    )["total"]


def _taken_out(prefix: str = ""):
    """The signed sum of the rows that took money out of a payment: a
    refund or a chargeback takes it out, a refund reversal (a refund the
    provider reported failed) gives it back."""
    amount = f"{prefix}amount"
    return Coalesce(
        Sum(
            Case(
                When(
                    **{f"{prefix}kind": PaymentTransactionKind.REFUND_REVERSAL},
                    then=-F(amount),
                ),
                default=F(amount),
                output_field=_AMOUNT,
            )
        ),
        _ZERO,
    )


class PaymentLedger:
    @staticmethod
    def captured(order: Order) -> Decimal:
        """What was received for *order*: what providers captured and what
        couriers collected in cash."""
        return _total(
            PaymentTransaction.objects.filter(
                order=order, kind__in=_RECEIPT_KINDS
            )
        )

    @staticmethod
    def refunded(order: Order, *, before: datetime | None = None) -> Decimal:
        """What was paid back out of *order*'s payments, by a provider or
        by the merchant, less refunds a provider reported failed. With
        *before*, only the rows recorded before that moment."""
        rows = PaymentTransaction.objects.filter(
            order=order,
            kind__in=(
                PaymentTransactionKind.REFUND,
                PaymentTransactionKind.REFUND_REVERSAL,
            ),
        )
        if before is not None:
            rows = rows.filter(created_at__lt=before)
        return rows.aggregate(total=_taken_out())["total"]

    @staticmethod
    def charged_back(order: Order) -> Decimal:
        """What card networks took back out of *order*'s payments after a
        lost dispute."""
        return _total(
            PaymentTransaction.objects.filter(
                order=order, kind=PaymentTransactionKind.CHARGEBACK
            )
        )

    @staticmethod
    def chargebacks_returned(
        order: Order, *, before: datetime | None = None
    ) -> Decimal:
        """What lost disputes took back on *order* where staff decided the
        goods came back (``ChargebackDecision``): the part of a chargeback
        that reverses the sale, as a refund does. With *before*, only the
        decisions made before that moment."""
        rows = PaymentTransaction.objects.filter(
            order=order,
            kind=PaymentTransactionKind.CHARGEBACK,
            decision__goods_returned=True,
        )
        if before is not None:
            rows = rows.filter(decision__decided_at__lt=before)
        return _total(rows)

    @staticmethod
    def dispute_open(order: Order, metadata: dict | None) -> bool:
        """Whether a cardholder dispute against *order*'s payment is open,
        from the ``disputed`` / ``dispute_id`` / ``dispute_status`` flags
        the provider handlers keep in ``Order.metadata`` (*metadata* is
        passed in so a caller holding the order lock reads it fresh).

        Stripe ("you can't issue a refund on a disputed charge until your
        customer's card issuer decides in your favor",
        docs.stripe.com/disputes/withdrawing) and Viva (a disputed payment
        cannot be refunded by the merchant) both refuse a refund meanwhile.
        An inquiry (``warning_*``) is not a dispute: Stripe lets the
        merchant refund it to resolve it. A dispute that closed against the
        merchant is no longer open: its CHARGEBACK row, keyed on the
        dispute id, says so.
        """
        metadata = metadata or {}
        dispute_id = metadata.get("dispute_id")
        if not metadata.get("disputed") or not dispute_id:
            return False
        if str(metadata.get("dispute_status", "")).startswith("warning_"):
            return False
        return not PaymentTransaction.objects.filter(
            order=order,
            kind=PaymentTransactionKind.CHARGEBACK,
            provider_reference=dispute_id,
        ).exists()

    @classmethod
    def fully_returned(cls, order: Order) -> bool:
        """Whether refunds and chargebacks together cover everything that
        was received for *order*. The one place that decides it, for what
        hangs off the order's total (the loyalty points)."""
        captured = cls.captured(order)
        return (
            captured > 0
            and cls.refunded(order) + cls.charged_back(order) >= captured
        )

    @staticmethod
    def refunded_on(charge: PaymentTransaction) -> Decimal:
        """What has been refunded out of this one payment (a chargeback is
        not a refund: Stripe's ``charge.refunded`` reports refunds only),
        less refunds Stripe reported failed."""
        return charge.refunds.filter(
            kind__in=(
                PaymentTransactionKind.REFUND,
                PaymentTransactionKind.REFUND_REVERSAL,
            )
        ).aggregate(total=_taken_out())["total"]

    @staticmethod
    def reversed_after(charge: PaymentTransaction, event_time) -> bool:
        """Whether a refund on *charge* was reported failed after the
        provider event stamped *event_time* was raised.

        A cumulative refunded figure from before the failure still counts
        the failed refund, and bringing the ledger up to it would book the
        reversed money as refunded again. Providers do not deliver events
        in order, so such an event is recognised by its own timestamp and
        ignored. No timestamp, no judgement: ``False``."""
        if not isinstance(event_time, datetime):
            return False
        return charge.refunds.filter(
            kind=PaymentTransactionKind.REFUND_REVERSAL,
            created_at__gt=event_time,
        ).exists()

    @staticmethod
    def reserved_on(charge: PaymentTransaction) -> Decimal:
        """What live refund attempts hold back from this one payment."""
        return _total(charge.refund_attempts.live())

    @classmethod
    def balances(cls, order: Order) -> list[ChargeBalance]:
        """Every payment of *order* with money left in it, oldest first: a
        provider's CHARGE or a courier's COD_COLLECTION. Money a chargeback
        took back, and money a live refund attempt holds, is not left in
        it."""
        charges = (
            PaymentTransaction.objects.filter(
                order=order, kind__in=_RECEIPT_KINDS
            )
            .annotate(already_returned=_taken_out("refunds__"))
            .order_by("created_at", "id")
        )
        # Not a second join on the same query: two multi-valued relations
        # would multiply each other's rows.
        reserved = dict(
            RefundAttempt.objects.live()
            .filter(order=order, kind=RefundAttemptKind.PROVIDER)
            .values_list("charge")
            .annotate(held=Sum("amount", output_field=_AMOUNT))
        )
        balances = []
        for charge in charges:
            left = (
                charge.amount.amount
                - charge.already_returned
                - reserved.get(charge.pk, _ZERO)
            )
            if left > 0:
                balances.append(ChargeBalance(charge, left))
        return balances

    @classmethod
    def refundable(cls, order: Order) -> Decimal:
        """What can still be refunded for *order*: received minus refunded,
        payment by payment (a refund is made against one payment)."""
        return sum((b.refundable for b in cls.balances(order)), _ZERO)

    @staticmethod
    def stored_value(
        order: Order, *, before: datetime | None = None
    ) -> StoredValue:
        """What *order* paid with gift cards and points, and what has gone
        back (:class:`StoredValue`). With *before*, what had gone back by
        that moment."""
        from giftcard.enum import GiftCardTransactionKind
        from giftcard.models import GiftCardTransaction
        from loyalty.enum import TransactionType
        from loyalty.models.transaction import PointsTransaction

        cut = {} if before is None else {"created_at__lt": before}

        def gift_card(kind) -> Decimal:
            return GiftCardTransaction.objects.filter(
                order=order, kind=kind, **cut
            ).aggregate(total=Coalesce(Sum("amount"), _ZERO))["total"]

        def points(**filters) -> int:
            return PointsTransaction.objects.filter(
                reference_order=order, **filters, **cut
            ).aggregate(total=Coalesce(Sum("points"), 0))["total"]

        # An order that paid with neither has nothing to read: the common
        # case costs no query.
        has_gift_card = bool(order.gift_card_amount.amount)
        has_points = bool(order.loyalty_discount.amount)
        return StoredValue(
            gift_card_total=(
                -gift_card(GiftCardTransactionKind.REDEEM)
                if has_gift_card
                else _ZERO
            ),
            gift_card_returned=(
                gift_card(GiftCardTransactionKind.REFUND_CREDIT)
                if has_gift_card
                else _ZERO
            ),
            points_spent=(
                -points(transaction_type=TransactionType.REDEEM)
                if has_points
                else 0
            ),
            points_restored=(
                points(transaction_type=TransactionType.ADJUST, points__gt=0)
                if has_points
                else 0
            ),
            points_value=order.loyalty_discount.amount,
        )

    @staticmethod
    def stored_returned_by(order: Order, attempt: RefundAttempt) -> Money:
        """What *attempt* actually put back onto gift cards and points, read
        from the rows that name it. Not the amount it claimed: a cancel or a
        racing refund may have returned part of it first, and the ledgers
        only credit what is still owed."""
        from giftcard.models import GiftCardTransaction
        from loyalty.models.transaction import PointsTransaction

        gift_card = GiftCardTransaction.objects.filter(
            refund_attempt=attempt
        ).aggregate(total=Coalesce(Sum("amount"), _ZERO))["total"]
        points = PointsTransaction.objects.filter(
            refund_attempt=attempt
        ).aggregate(total=Coalesce(Sum("points"), 0))["total"]
        stored = PaymentLedger.stored_value(order)
        points_value = (
            (stored.points_value * points / stored.points_spent).quantize(
                Decimal("0.01")
            )
            if points and stored.points_spent
            else _ZERO
        )
        return Money(gift_card + points_value, attempt.amount.currency)

    @staticmethod
    def stored_returned_at(attempt: RefundAttempt) -> datetime | None:
        """When *attempt* put value back: the first row that names it (the
        same clock ``stored_value(before=...)`` cuts by)."""
        from giftcard.models import GiftCardTransaction
        from loyalty.models.transaction import PointsTransaction

        moments = [
            qs.order_by("created_at")
            .values_list("created_at", flat=True)
            .first()
            for qs in (
                GiftCardTransaction.objects.filter(refund_attempt=attempt),
                PointsTransaction.objects.filter(refund_attempt=attempt),
            )
        ]
        moments = [m for m in moments if m is not None]
        return min(moments) if moments else None

    @classmethod
    def stored_value_refundable(cls, order: Order) -> Decimal:
        """What can still go back onto gift cards and points: what has not
        gone back, less what live stored-value attempts hold."""
        held = _total(
            RefundAttempt.objects.live().filter(
                order=order, kind=RefundAttemptKind.STORED_VALUE
            )
        )
        return max(cls.stored_value(order).left - held, _ZERO)

    @classmethod
    def refundable_value(cls, order: Order) -> Decimal:
        """The value that can still be refunded for *order*: the
        provider's money (:meth:`refundable`) first, then what the order
        paid with gift cards and points."""
        return cls.refundable(order) + cls.stored_value_refundable(order)

    @classmethod
    def refund_open(cls, order: Order) -> bool:
        """Whether money paid for *order* can still be refunded.

        Either the ledger holds refundable money, or the order was paid
        through a registered provider whose capture the ledger never
        recorded: ``OrderService.refund_order`` reads that from the provider
        once (:meth:`ensure_charge`), so the money is refundable although
        :meth:`refundable` is 0. A cheap predicate on purpose (no provider
        call): it decides whether an admin button is drawn and whether an
        order may be closed as REFUNDED. An order paid without a provider
        (cash, gift cards, points) is not open: nothing can be refunded
        through one.
        """
        from order.payment import registered_provider_codes

        if cls.refundable_value(order) > 0:
            return True
        pay_way = order.pay_way
        return bool(
            order.payment_id
            and pay_way is not None
            and pay_way.provider_code in registered_provider_codes()
            and order.payment_status in _HELD_PAYMENT_STATUSES
            and not cls.captured(order)
        )

    @classmethod
    def refund_status(cls, order: Order) -> PaymentStatus:
        """The payment status of an order that has had a refund.

        REFUNDED means nothing is left to return: refunds and chargebacks
        cover what a provider or a courier received, AND what the order paid
        with gift cards and points has gone back (:meth:`stored_value`). It
        is the state that releases what hangs off the order's total (the
        loyalty points it earned are reversed once, with the last refund).
        Below that the order is PARTIALLY_REFUNDED, and an order whose every
        refund was reversed (the provider reported them failed) and nothing
        else returned is back to COMPLETED. Derived from the ledgers' sums
        and nothing else, so it cannot go backwards the way a status
        written by each path could.
        """
        refunded = cls.refunded(order)
        stored = cls.stored_value(order)
        if refunded <= 0 and stored.returned <= 0:
            return PaymentStatus.COMPLETED
        if (
            refunded + cls.charged_back(order) >= cls.captured(order)
            and stored.left <= 0
        ):
            return PaymentStatus.REFUNDED
        return PaymentStatus.PARTIALLY_REFUNDED

    @classmethod
    def plan_refund(
        cls, order: Order, amount: Money | None
    ) -> list[tuple[PaymentTransaction, Money]]:
        """Which payments a refund of *amount* is made against, and how much
        of each; ``None`` plans everything that can be refunded.

        A refund goes against one payment, so an amount larger than any
        single payment's remainder is split across payments, oldest first.
        A share against cash a courier collected (:func:`is_cash_collection`)
        has no provider to refund it: the merchant pays it back and says
        how (:meth:`record_cod_refund`). The caller has already refused an
        amount above :meth:`refundable`.
        """
        plan: list[tuple[PaymentTransaction, Money]] = []
        left = None if amount is None else amount.amount
        for charge, remainder in cls.balances(order):
            share = remainder if left is None else min(remainder, left)
            plan.append((charge, Money(share, charge.amount.currency)))
            if left is not None:
                left -= share
                if left <= 0:
                    break
        return plan

    @staticmethod
    def _record(
        order: Order,
        *,
        kind: PaymentTransactionKind,
        amount: Money,
        provider: str,
        reference: str,
        **extra,
    ) -> tuple[PaymentTransaction, bool]:
        row, created = PaymentTransaction.objects.get_or_create(
            provider=provider,
            provider_reference=reference,
            kind=kind,
            defaults={"order": order, "amount": amount, **extra},
        )
        if row.order_id != order.pk:
            logger.error(
                "Payment %s %s %r is already recorded for order %s, not "
                "order %s",
                provider,
                kind,
                reference,
                row.order_id,
                order.pk,
            )
        if created:
            PaymentLedger._apply_pending_fee(row)
        return row, created

    @staticmethod
    def _apply_pending_fee(row: PaymentTransaction) -> None:
        """Move a fee the provider reported before *row* existed onto it."""
        with transaction.atomic():
            pending = (
                PendingPaymentFee.objects.select_for_update()
                .filter(
                    provider=row.provider,
                    provider_reference__iexact=row.provider_reference,
                )
                .first()
            )
            if pending is None:
                return
            PaymentLedger.record_fee(
                provider=pending.provider,
                reference=pending.provider_reference,
                fee=pending.fee,
                source=PaymentFeeSource(pending.fee_source),
            )
            pending.delete()

    @classmethod
    def record_charge(
        cls, order: Order, *, amount: Money, provider: str, reference: str
    ) -> tuple[PaymentTransaction, bool]:
        """Record that *provider* captured *amount* under *reference* (the
        PaymentIntent id, the Viva transaction id)."""
        return cls._record(
            order,
            kind=PaymentTransactionKind.CHARGE,
            amount=amount,
            provider=provider,
            reference=reference,
        )

    @staticmethod
    def record_fee(
        *,
        provider: str,
        reference: str,
        fee: Money,
        source: PaymentFeeSource,
    ) -> PaymentTransaction | None:
        """Write the fee *provider* reported for the movement it knows as
        *reference*, onto that ledger row. A fee for a movement the ledger
        does not hold yet is kept (``PendingPaymentFee``) and written when
        the row is recorded, so a report that arrives first is not lost.
        ``None`` when no row carries the fee now: nothing is invented or
        converted, and a fee in another currency than the row is not written.

        The provider's latest figure replaces an earlier one, so a
        redelivered or corrected report cannot add up twice. It is the only
        update a ledger row ever gets, and it touches the fee columns alone.
        The reference is matched without regard to case, as Viva's transaction
        ids are elsewhere in this module.
        """
        if fee.amount < 0:
            raise ValueError(f"A provider fee is never negative: {fee}")
        with transaction.atomic():
            row = (
                PaymentTransaction.objects.select_for_update()
                .filter(provider=provider, provider_reference__iexact=reference)
                .first()
            )
            if row is None:
                pending = (
                    PendingPaymentFee.objects.select_for_update()
                    .filter(
                        provider=provider, provider_reference__iexact=reference
                    )
                    .first()
                )
                if pending is None:
                    PendingPaymentFee.objects.create(
                        provider=provider,
                        provider_reference=reference,
                        fee=fee,
                        fee_source=source,
                    )
                else:
                    pending.fee = fee
                    pending.fee_source = source
                    pending.save(
                        update_fields=["fee", "fee_currency", "fee_source"]
                    )
                return None
            if row.amount.currency != fee.currency:
                logger.error(
                    "Payment %s %r is %s but its fee is reported in %s: "
                    "fee not recorded",
                    provider,
                    reference,
                    row.amount.currency,
                    fee.currency,
                )
                return None
            row.fee = fee
            row.fee_source = source
            row.save(update_fields=["fee", "fee_currency", "fee_source"])
        return row

    @classmethod
    def record_cod_collection(
        cls, order: Order, *, amount: Money, provider: str, reference: str
    ) -> None:
        """Record that a courier collected *amount* in cash for *order*.

        Nothing is recorded for a zero amount: a cash-on-delivery order
        its deductions cover in full has no cash to collect.
        """
        if amount.amount > 0:
            cls._record(
                order,
                kind=PaymentTransactionKind.COD_COLLECTION,
                amount=amount,
                provider=provider,
                reference=reference,
            )

    @classmethod
    def record_offline_collection(
        cls, order: Order, *, amount: Money, reference: str, description: str
    ) -> tuple[PaymentTransaction, bool]:
        """Record that *amount* arrived off the platform (a bank transfer)
        under the bank's *reference*."""
        return cls._record(
            order,
            kind=PaymentTransactionKind.OFFLINE_COLLECTION,
            amount=amount,
            provider=OFFLINE_TRANSFER_PROVIDER,
            reference=reference,
            description=description[:255],
        )

    @classmethod
    def record_refund(
        cls,
        order: Order,
        *,
        charge: PaymentTransaction,
        amount: Money,
        reference: str,
        description: str = "",
        provider: str | None = None,
        attempt: RefundAttempt | None = None,
    ) -> tuple[PaymentTransaction, bool]:
        """Record that *charge* was refunded *amount* under *reference* (the
        provider's refund id), and bring ``order.payment_status`` in line.
        *provider* is who moved the money: the charge's own provider unless
        the refund was made outside it.

        *attempt* is the claim this refund fulfils. A refund booked without
        one (a provider's webhook) settles the oldest live attempt on the
        charge for the same amount, so the attempt that made it cannot book
        it a second time (:meth:`settle_attempt`).

        The status is written here, from the ledger, whoever the writer is,
        so no path decides REFUNDED or PARTIALLY_REFUNDED for itself. A
        refund the ledger already holds (the provider notifies refunds this
        app made, and redelivers events) changes nothing and returns
        ``created=False``.
        """
        row, created = cls._record(
            order,
            kind=PaymentTransactionKind.REFUND,
            amount=amount,
            provider=provider or charge.provider,
            reference=reference,
            charge=charge,
            description=description[:255],
        )
        if created:
            order.payment_status = cls.refund_status(order)
            order.save(update_fields=["payment_status"])
        if attempt is None and created:
            attempt = (
                RefundAttempt.objects.live()
                .filter(charge=charge, amount=amount)
                .order_by("created_at", "id")
                .first()
            )
        if attempt is not None:
            cls.settle_attempt(attempt, row)
        return row, created

    @staticmethod
    def settle_attempt(
        attempt: RefundAttempt, refund: PaymentTransaction
    ) -> None:
        """The ledger holds the refund *attempt* was claimed for. A refund
        row already answering another attempt is not linked twice: this one
        is settled without a link."""
        attempt.status = RefundAttemptStatus.SETTLED
        fields = ["status"]
        if (
            not RefundAttempt.objects.filter(refund=refund)
            .exclude(pk=attempt.pk)
            .exists()
        ):
            attempt.refund = refund
            fields.append("refund")
        attempt.save(update_fields=fields)

    @classmethod
    def record_refund_reversal(
        cls,
        order: Order,
        *,
        charge: PaymentTransaction,
        amount: Money,
        reference: str,
        description: str = "",
    ) -> tuple[PaymentTransaction, bool]:
        """Record that a refund the ledger counted was reported failed by
        the provider (*reference* is the provider's refund id): the money
        was never paid back, so it counts as held again, and
        ``order.payment_status`` is re-derived. Nothing is written for a
        refund already reversed (providers redeliver) or when the ledger
        holds no refund on *charge* to reverse."""
        existing = PaymentTransaction.objects.filter(
            provider=charge.provider,
            provider_reference=reference,
            kind=PaymentTransactionKind.REFUND_REVERSAL,
        ).first()
        if existing is not None:
            return existing, False
        reversible = cls.refunded_on(charge)
        if reversible <= 0:
            raise ValueError(
                f"Payment {charge.provider_reference} holds no refund to "
                f"reverse for {reference}"
            )
        row, created = cls._record(
            order,
            kind=PaymentTransactionKind.REFUND_REVERSAL,
            amount=Money(
                min(amount.amount, reversible), charge.amount.currency
            ),
            provider=charge.provider,
            reference=reference,
            charge=charge,
            description=description[:255],
        )
        if created:
            order.payment_status = cls.refund_status(order)
            order.save(update_fields=["payment_status"])
        return row, created

    @staticmethod
    def left_on(charge: PaymentTransaction) -> Decimal:
        """What is still in this one payment: captured minus what refunds
        and chargebacks took out of it."""
        return (
            charge.amount.amount
            - charge.refunds.aggregate(total=_taken_out())["total"]
        )

    @classmethod
    def record_chargeback(
        cls,
        order: Order,
        *,
        charge: PaymentTransaction,
        amount: Money,
        reference: str,
        description: str = "",
    ) -> ChargebackOutcome:
        """Record that a lost dispute took *amount* back out of *charge*,
        keyed on the provider's dispute id *reference*.

        Never more than the payment still holds: *amount* is capped at what
        is left of *charge* (:meth:`left_on`), because money already refunded
        or charged back cannot be taken again. Stripe reports the disputed
        amount, which should fit; Viva reports only the whole sale, so a
        payment partly refunded before the dispute is capped. A cap is
        logged. When nothing is left no row is written
        (``row`` is ``None``): the caller still tells staff.

        The money is gone, so it leaves what can be refunded: staff cannot
        refund it a second time. ``payment_status`` is NOT touched and
        ``order_refunded`` is NOT sent: nothing was paid back to the
        customer, so there is no refund email, no Meta ``Refund`` event and
        no gift-card release. The one side effect is
        ``order_charged_back``, sent when a row is new, which the loyalty
        app answers by reversing the points once refunds and chargebacks
        cover the whole payment. A dispute the ledger already holds
        (providers redeliver) changes nothing and returns ``created=False``.
        """
        left = cls.left_on(charge)
        returned = charge.amount.amount - left
        existing = PaymentTransaction.objects.filter(
            provider=charge.provider,
            provider_reference=reference,
            kind=PaymentTransactionKind.CHARGEBACK,
        ).first()
        if existing is not None:
            return ChargebackOutcome(
                existing, False, amount, existing.amount, returned
            )

        recorded = Money(
            max(min(amount.amount, left), _ZERO), charge.amount.currency
        )
        if recorded.amount < amount.amount:
            logger.warning(
                "Chargeback %s of %s on order %s exceeds the %s left on "
                "payment %s: recording %s",
                reference,
                amount,
                order.pk,
                left,
                charge.provider_reference,
                recorded,
            )
        if recorded.amount <= 0:
            return ChargebackOutcome(None, False, amount, recorded, returned)

        row, created = cls._record(
            order,
            kind=PaymentTransactionKind.CHARGEBACK,
            amount=recorded,
            provider=charge.provider,
            reference=reference,
            charge=charge,
            description=description[:255],
        )
        if created:
            order_charged_back.send(
                sender=type(order),
                order=order,
                amount=recorded,
                chargeback=row,
            )
        return ChargebackOutcome(row, created, amount, recorded, returned)

    @staticmethod
    def cod_refund_reference(
        order: Order, collection: PaymentTransaction, reference: str
    ) -> str:
        """The ledger key of a refund of *collection* paid back under
        *reference* (a bank transfer id): scoped to the order and the
        collection, so one transfer id cannot be booked twice against the
        same cash and two orders can never collide on it."""
        return f"order-{order.pk}-{collection.pk}-{reference}"

    @classmethod
    def has_cod_refund(
        cls, order: Order, collection: PaymentTransaction, reference: str
    ) -> bool:
        """Whether *collection* was already refunded under *reference*."""
        return PaymentTransaction.objects.filter(
            provider=MANUAL_REFUND_PROVIDER,
            kind=PaymentTransactionKind.REFUND,
            provider_reference=cls.cod_refund_reference(
                order, collection, reference
            ),
        ).exists()

    @classmethod
    def record_cod_refund(
        cls,
        order: Order,
        *,
        collection: PaymentTransaction,
        amount: Money,
        reference: str,
        description: str = "",
        attempt: RefundAttempt | None = None,
    ) -> tuple[PaymentTransaction, bool]:
        """Record that the merchant paid *amount* of the cash *collection*
        back to the customer themselves, under *reference* (how: a bank
        transfer id). The same status derivation as any refund
        (:meth:`record_refund`)."""
        return cls.record_refund(
            order,
            charge=collection,
            amount=amount,
            reference=cls.cod_refund_reference(order, collection, reference),
            description=description,
            provider=MANUAL_REFUND_PROVIDER,
            attempt=attempt,
        )

    @classmethod
    def ensure_charge(
        cls, order: Order, *, provider_code: str, payment_id: str
    ) -> PaymentTransaction | None:
        """The ledger's CHARGE for *payment_id*, read from the provider and
        recorded when the ledger has none yet.

        Charges are recorded where a provider confirms them (the payment
        webhooks), but an order can be paid without that row: the payment
        settled while the previous release was still serving, or the
        order was created from an already-succeeded intent and its webhook
        never reached it. The first refund that needs the capture reads it
        from the provider once; from then on the ledger answers.

        ``None`` when the provider cannot confirm a capture (it has logged
        why): nothing is recorded and the caller refuses.
        """
        from order.payment import get_payment_provider

        existing = PaymentTransaction.objects.filter(
            order=order,
            kind=PaymentTransactionKind.CHARGE,
            provider=provider_code,
            provider_reference=payment_id,
        ).first()
        if existing is not None:
            return existing

        status, data = get_payment_provider(provider_code).get_payment_status(
            payment_id
        )
        captured = data.get("amount")
        if status not in _CAPTURED_STATUSES or captured is None:
            logger.error(
                "Payment %s %s cannot be recorded as captured for order %s: "
                "provider says %s",
                provider_code,
                payment_id,
                order.pk,
                status,
            )
            return None
        row, _created = cls.record_charge(
            order,
            amount=Money(
                Decimal(str(captured)),
                data.get("currency") or order.amount_due.currency,
            ),
            provider=provider_code,
            reference=payment_id,
        )
        return row
