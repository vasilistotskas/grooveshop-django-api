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

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import PaymentStatus
from order.models.payment_transaction import PaymentTransaction
from order.models.refund_attempt import RefundAttempt, RefundAttemptStatus
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
)

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
    """Money a courier collected in cash: there is no provider to refund it
    through, so the merchant pays it back themselves."""
    return row.kind == PaymentTransactionKind.COD_COLLECTION


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
    def refunded(order: Order) -> Decimal:
        """What was paid back out of *order*'s payments, by a provider or
        by the merchant, less refunds a provider reported failed."""
        return PaymentTransaction.objects.filter(
            order=order,
            kind__in=(
                PaymentTransactionKind.REFUND,
                PaymentTransactionKind.REFUND_REVERSAL,
            ),
        ).aggregate(total=_taken_out())["total"]

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
            .filter(order=order)
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

        if cls.refundable(order) > 0:
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

        REFUNDED means nothing is left to refund: refunds and chargebacks
        together cover what was received. It is the only state that
        releases what hangs off the order's total (the gift-card
        redemption and the loyalty points are returned once, with the last
        refund, not in slices). Below that the order is PARTIALLY_REFUNDED,
        and an order whose every refund was reversed (the provider reported
        them failed) is back to COMPLETED. Derived from the ledger's sums
        and nothing else, so it cannot go backwards the way a status
        written by each path could.
        """
        refunded = cls.refunded(order)
        if refunded <= 0:
            return PaymentStatus.COMPLETED
        if refunded + cls.charged_back(order) >= cls.captured(order):
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
        return row, created

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
