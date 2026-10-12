"""The payment ledger: what providers moved for an order, and what follows.

``PaymentTransaction`` rows are the only record of the money captured for
an order and the money refunded from it. Everything else is read from
them: how much can still be refunded, which payment a refund goes
against, and whether ``Order.payment_status`` is REFUNDED or
PARTIALLY_REFUNDED (:meth:`PaymentLedger.refund_status`, the one place
that decides it, for the in-app refund, the Stripe ``charge.refunded``
webhook and the Viva reversal webhook alike).

Every ``record_*`` method must run with the order row locked by the
caller (``select_for_update``): the sums read here and the row written
here have to be one step. Rows are keyed on the provider's own id for the
movement (``unique_payment_transaction_reference``), so a delivery
repeated by the provider records nothing the second time and says so by
returning ``created=False``.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple

from django.db.models import DecimalField, QuerySet, Sum
from django.db.models.functions import Coalesce
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import PaymentStatus
from order.models.payment_transaction import PaymentTransaction

if TYPE_CHECKING:
    from order.models.order import Order

logger = logging.getLogger(__name__)

_ZERO = Decimal(0)
_AMOUNT = DecimalField(max_digits=11, decimal_places=2)

#: What a provider can say about a payment that was captured. Viva reports
#: ``R`` for a payment that was refunded afterwards, which is still a
#: capture; Stripe keeps a refunded PaymentIntent ``succeeded``.
_CAPTURED_STATUSES = frozenset(
    {PaymentStatus.COMPLETED, PaymentStatus.REFUNDED}
)


class ChargeBalance(NamedTuple):
    """A payment and how much of it can still be refunded."""

    charge: PaymentTransaction
    refundable: Decimal


def _total(rows: QuerySet[PaymentTransaction]) -> Decimal:
    return rows.aggregate(
        total=Coalesce(Sum("amount", output_field=_AMOUNT), _ZERO)
    )["total"]


class PaymentLedger:
    @staticmethod
    def captured(order: Order) -> Decimal:
        """What providers captured for *order*."""
        return _total(
            PaymentTransaction.objects.filter(
                order=order, kind=PaymentTransactionKind.CHARGE
            )
        )

    @staticmethod
    def refunded(order: Order) -> Decimal:
        """What providers refunded out of *order*'s payments."""
        return _total(
            PaymentTransaction.objects.filter(
                order=order, kind=PaymentTransactionKind.REFUND
            )
        )

    @staticmethod
    def refunded_on(charge: PaymentTransaction) -> Decimal:
        """What has been refunded out of this one payment."""
        return _total(charge.refunds.all())

    @classmethod
    def balances(cls, order: Order) -> list[ChargeBalance]:
        """Every payment of *order* with money left in it, oldest first."""
        charges = (
            PaymentTransaction.objects.filter(
                order=order, kind=PaymentTransactionKind.CHARGE
            )
            .annotate(
                already_refunded=Coalesce(
                    Sum("refunds__amount", output_field=_AMOUNT), _ZERO
                )
            )
            .order_by("created_at", "id")
        )
        balances = []
        for charge in charges:
            left = charge.amount.amount - charge.already_refunded
            if left > 0:
                balances.append(ChargeBalance(charge, left))
        return balances

    @classmethod
    def refundable(cls, order: Order) -> Decimal:
        """What can still be refunded for *order*: captured minus refunded,
        payment by payment (a refund is made against one payment)."""
        return sum((b.refundable for b in cls.balances(order)), _ZERO)

    @classmethod
    def refund_status(cls, order: Order) -> PaymentStatus:
        """The payment status of an order that has had a refund.

        REFUNDED means nothing is left to refund, and it is the only state
        that releases what hangs off the order's total (the gift-card
        redemption and the loyalty points are returned once, with the last
        refund, not in slices). Below that the order is PARTIALLY_REFUNDED.
        Derived from the ledger's sums and nothing else, so it cannot go
        backwards the way a status written by each path could.
        """
        if cls.refunded(order) >= cls.captured(order):
            return PaymentStatus.REFUNDED
        return PaymentStatus.PARTIALLY_REFUNDED

    @staticmethod
    def next_refund_number(order: Order) -> int:
        """The ordinal of the next refund of *order*, for the Stripe
        idempotency key. Counted under the order lock, so two staff members
        refunding at once cannot be handed the same number, and an attempt
        that wrote no row (a timeout, an error that rolled back) is
        retried under the key it already used."""
        return (
            PaymentTransaction.objects.filter(
                order=order, kind=PaymentTransactionKind.REFUND
            ).count()
            + 1
        )

    @classmethod
    def plan_refund(
        cls, order: Order, amount: Money | None
    ) -> list[tuple[PaymentTransaction, Money]]:
        """Which payments a refund of *amount* is made against, and how much
        of each; ``None`` plans everything that can be refunded.

        A refund goes against one payment, so an amount larger than any
        single payment's remainder is split across payments, oldest first.
        The caller has already refused an amount above
        :meth:`refundable`.
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
    ) -> tuple[PaymentTransaction, bool]:
        """Record that *charge* was refunded *amount* under *reference* (the
        provider's refund id), and bring ``order.payment_status`` in line.

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
            provider=charge.provider,
            reference=reference,
            charge=charge,
            description=description[:255],
        )
        if created:
            order.payment_status = cls.refund_status(order)
            order.save(update_fields=["payment_status"])
        return row, created

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
