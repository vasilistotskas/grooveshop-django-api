"""Retail credit documents (myDATA 11.4): when one is owed, and what it says.

ΕΛΠ art. 12(4): "in the case of discounts or refunds a retail credit
document is issued". The platform's retail receipt is the order's
:class:`~order.models.invoice.Invoice` (11.1, issued when the order
ships). Once that receipt carries a MARK, money going back owes a credit
document:

* every REFUND row on the payment ledger gets one, for exactly the amount
  refunded (``PaymentLedger.record_refund`` is where every refund path
  ends, so this is the one place to read them from);
* an order that closes without a sale after its receipt was issued (a
  collect-on-delivery parcel that came back, a fully refunded order whose
  receipt also covered a gift card) gets ONE settlement document for what
  the receipt still holds and no credit has covered.

A refund recorded before the receipt existed has no document: the receipt
is issued net of it (``order.invoicing.refunded_before_receipt``), so there
is nothing to credit; a chargeback never has one (the sale was not reversed
by the merchant).

:func:`create_pending_credits` is the single reconciliation: it looks at
the ledger and the documents under the order lock and creates whatever is
missing, so every trigger (a refund, an order closing, the receipt being
registered) calls the same idempotent function.

**How a credit splits over VAT rates.** The receipt froze its gross per
rate (``Invoice.vat_breakdown``: goods, shipping and fees all in it). A
credit spreads its amount over the rates in proportion to what each still
holds, to the cent (``order.discounts.allocate_capped``), so a partial
refund of a mixed 24%/13% order credits both rates.

**Nothing but this module stops an over-credit.** AADE forbids
``correlatedInvoices`` on an 11.4 (error 205, confirmed against the dev API
on 2026-10-12), so the credit is not linked to its receipt at AADE and AADE
cannot check it against the receipt's total (error 319 belongs to linked
credits). The link lives in our ``RetailCredit.invoice`` row only, and the
cap below is the sole guard: a credit the receipt cannot hold is never
submitted. The NET of a credit is derived cumulatively: what the rate's total credited gross splits to
(``order.taxation.split_gross``), capped at the receipt's net for the
rate, less the net already credited. Rounding can therefore never push
several partial credits past the receipt, and the last one lands exactly on
it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone
from djmoney.money import Money

from order.discounts import allocate_capped
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import PaymentStatus
from order.models import (
    CreditCounter,
    Invoice,
    Order,
    OrderHistory,
    PaymentTransaction,
    RetailCredit,
)
from order.models.invoice import MyDataStatus
from order.models.refund_attempt import (
    RefundAttempt,
    RefundAttemptKind,
    RefundAttemptStatus,
)
from order.models.retail_credit import CreditReason
from order.payment_ledger import PaymentLedger
from order.taxation import split_gross

logger = logging.getLogger(__name__)

#: Payment statuses in which an order has no sale left to keep.
SETTLEMENT_PAYMENT_STATUSES = frozenset(
    {PaymentStatus.CANCELED, PaymentStatus.REFUNDED}
)

_ZERO = Decimal(0)


class CreditExceedsReceiptError(ValueError):
    """The amount to credit is more than the receipt still holds."""


def credit_breakdown(
    receipt_rows: Iterable[dict[str, str]],
    prior_credit_rows: Iterable[Sequence[dict[str, str]]],
    amount: Decimal,
) -> list[dict[str, str]]:
    """The VAT rows of a credit of ``amount`` (gross) against a receipt.

    ``receipt_rows`` is the receipt's ``vat_breakdown``;
    ``prior_credit_rows`` the ``vat_breakdown`` of every credit already
    issued against it. Returns ``{rate, subtotal, vat, gross}`` rows
    (strings, the shape ``Invoice.vat_breakdown`` stores), highest rate
    first. Raises :class:`CreditExceedsReceiptError` when the receipt does
    not hold ``amount`` any more.
    """
    rate_key: dict[Decimal, str] = {}
    receipt_gross: dict[Decimal, Decimal] = {}
    receipt_net: dict[Decimal, Decimal] = {}
    for row in receipt_rows:
        rate = Decimal(row["rate"])
        rate_key[rate] = row["rate"]
        receipt_gross[rate] = Decimal(row["gross"])
        receipt_net[rate] = Decimal(row["subtotal"])

    credited_gross: dict[Decimal, Decimal] = dict.fromkeys(receipt_gross, _ZERO)
    credited_net: dict[Decimal, Decimal] = dict.fromkeys(receipt_gross, _ZERO)
    for rows in prior_credit_rows:
        for row in rows:
            rate = Decimal(row["rate"])
            credited_gross[rate] += Decimal(row["gross"])
            credited_net[rate] += Decimal(row["subtotal"])

    remaining = {
        rate: receipt_gross[rate] - credited_gross[rate]
        for rate in receipt_gross
    }
    shares = allocate_capped(amount, remaining, remaining)
    if sum(shares.values(), _ZERO) != amount:
        raise CreditExceedsReceiptError(
            f"Cannot credit {amount}: the receipt holds "
            f"{sum((max(v, _ZERO) for v in remaining.values()), _ZERO)} "
            "more."
        )

    rows: list[dict[str, str]] = []
    for rate in sorted(shares, reverse=True):
        share = shares[rate]
        if share <= 0:
            continue
        total_gross = credited_gross[rate] + share
        if total_gross == receipt_gross[rate]:
            total_net = receipt_net[rate]
        else:
            total_net = min(
                split_gross(total_gross, rate)[0], receipt_net[rate]
            )
        net = total_net - credited_net[rate]
        rows.append(
            {
                "rate": rate_key[rate],
                "subtotal": str(net),
                "vat": str(share - net),
                "gross": str(share),
            }
        )
    return rows


def _create_credit(
    order: Order,
    invoice: Invoice,
    *,
    amount: Decimal,
    reason: CreditReason,
    refund: PaymentTransaction | None = None,
    refund_attempt: RefundAttempt | None = None,
) -> RetailCredit:
    prior = [
        credit.vat_breakdown
        for credit in RetailCredit.objects.filter(invoice=invoice).exclude(
            mydata_status=MyDataStatus.CANCELED
        )
    ]
    rows = credit_breakdown(invoice.vat_breakdown, prior, amount)
    net = sum((Decimal(r["subtotal"]) for r in rows), _ZERO)
    vat = sum((Decimal(r["vat"]) for r in rows), _ZERO)
    currency = invoice.currency
    issue_date = timezone.localdate()
    return RetailCredit.objects.create(
        order=order,
        invoice=invoice,
        reason=reason,
        refund=refund,
        refund_attempt=refund_attempt,
        credit_number=CreditCounter.allocate(issue_date.year),
        issue_date=issue_date,
        subtotal=Money(net, currency),
        total_vat=Money(vat, currency),
        total=Money(net + vat, currency),
        vat_breakdown=rows,
        currency=currency,
    )


def _credited_total(invoice: Invoice) -> Decimal:
    return sum(
        (
            Decimal(credit.total.amount)
            for credit in RetailCredit.objects.filter(invoice=invoice).exclude(
                mydata_status=MyDataStatus.CANCELED
            )
        ),
        _ZERO,
    )


def create_pending_credits(order_id: int) -> list[int]:
    """Create every credit document the order owes and does not have yet;
    returns their ids, oldest first, for the caller to submit.

    Idempotent: a refund already credited and a settlement already issued
    are skipped, so a second call (a retried task, a second trigger)
    creates nothing. Nothing is created until the receipt is registered
    with AADE (it has a MARK and was not canceled): a credit is linked to
    that MARK, and the receipt's own success calls this again.
    """
    created: list[int] = []
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        invoice = Invoice.objects.filter(order=order).first()
        if (
            invoice is None
            or not invoice.mydata_mark
            or invoice.mydata_status != MyDataStatus.CONFIRMED
        ):
            return created

        refunds = PaymentTransaction.objects.filter(
            order=order,
            kind=PaymentTransactionKind.REFUND,
            created_at__gte=invoice.created_at,
            credit_document__isnull=True,
        ).order_by("created_at", "id")
        for refund in refunds:
            created.extend(
                _guarded(
                    order,
                    invoice,
                    amount=Decimal(refund.amount.amount),
                    reason=CreditReason.REFUND,
                    refund=refund,
                )
            )

        # Value returned onto gift cards and points is a refund like any
        # other: the same credit, the same rule, one path. Its moment is
        # when its rows were written, the clock the receipt's cut-off uses.
        for attempt in RefundAttempt.objects.filter(
            order=order,
            kind=RefundAttemptKind.STORED_VALUE,
            status=RefundAttemptStatus.SETTLED,
            credit_document__isnull=True,
        ).order_by("id"):
            returned_at = PaymentLedger.stored_returned_at(attempt)
            returned = PaymentLedger.stored_returned_by(order, attempt)
            if (
                returned_at is None
                or returned_at < invoice.created_at
                or returned.amount <= 0
            ):
                continue
            created.extend(
                _guarded(
                    order,
                    invoice,
                    amount=Decimal(returned.amount),
                    reason=CreditReason.REFUND,
                    refund_attempt=attempt,
                )
            )

        if order.payment_status in SETTLEMENT_PAYMENT_STATUSES and not (
            RetailCredit.objects.filter(
                order=order, reason=CreditReason.SETTLEMENT
            ).exists()
        ):
            left = Decimal(invoice.total.amount) - _credited_total(invoice)
            if left > 0:
                created.extend(
                    _guarded(
                        order,
                        invoice,
                        amount=left,
                        reason=CreditReason.SETTLEMENT,
                        refund=None,
                    )
                )
    return created


def _guarded(order: Order, invoice: Invoice, **kwargs: Any) -> list[int]:
    """Create one credit, or record why it cannot exist.

    A credit larger than what its receipt holds is never sent: AADE does
    not link an 11.4 to its receipt, so it would accept it and register a
    document that disagrees with the receipt. Staff are told on the order.
    """
    try:
        with transaction.atomic():
            return [_create_credit(order, invoice, **kwargs).pk]
    except CreditExceedsReceiptError as exc:
        logger.error(
            "No credit document for order %s: %s",
            order.pk,
            exc,
            extra={"order_id": order.pk},
        )
        OrderHistory.log_note(
            order=order,
            note=f"No credit document could be issued: {exc}",
        )
        return []
