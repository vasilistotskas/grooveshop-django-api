"""What became of a refund attempt whose outcome is unknown.

A provider refund attempt stays PENDING when the call to the provider
ended without an answer (a timeout, a crash between the call and the
booking), and a PENDING attempt keeps holding its money and its place on the
receipt's VAT rates until something resolves it, however old it is. Three
things resolve it, each through the same ledger paths a refund made in one
go takes (``OrderService.book_share`` to settle, ``RefundAttempt`` to
decline):

* the provider's own webhook, when it books the refund (``record_refund``);
* :func:`reconcile_overdue`, which asks the provider once the attempt is
  overdue (``reconcile_after``) and settles or declines only when the answer
  is unambiguous;
* staff, through :func:`record_outcome`, for what the lookup cannot decide.

When the lookup cannot decide (nothing found, several candidates, a
provider that cannot be read) the attempt stays PENDING: it is never
guessed, and the store's dashboard counts it.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from order.enum.payment_transaction import PaymentTransactionKind
from order.exceptions import PaymentError
from order.models.history import OrderHistory
from order.models.order import Order
from order.models.payment_transaction import PaymentTransaction
from order.models.refund_attempt import RefundAttempt, RefundAttemptStatus
from order.payment import ProviderRefund, get_payment_provider
from order.payment_ledger import PaymentLedger, is_cash_collection
from order.services import OrderService

if TYPE_CHECKING:
    from djmoney.money import Money


logger = logging.getLogger(__name__)


class Resolution(StrEnum):
    SETTLED = "settled"
    DECLINED = "declined"
    UNRESOLVED = "unresolved"


def _lock(attempt: RefundAttempt) -> RefundAttempt | None:
    """The attempt re-read under its order's lock, or ``None`` when it has
    been resolved meanwhile (a webhook, staff, a retry)."""
    Order.objects.select_for_update().values_list("pk", flat=True).get(
        pk=attempt.order_id
    )
    locked = (
        RefundAttempt.objects.select_for_update(of=("self",))
        .select_related("charge")
        .get(pk=attempt.pk)
    )
    return locked if locked.status == RefundAttemptStatus.PENDING else None


def _decline(attempt: RefundAttempt, note: str, user=None) -> bool:
    """The provider did not make this refund: the attempt lets go of its
    money (and its allocation, which stops counting with it). ``False``
    when it was resolved meanwhile."""
    with transaction.atomic():
        locked = _lock(attempt)
        if locked is None:
            return False
        locked.status = RefundAttemptStatus.DECLINED
        locked.save(update_fields=["status"])
        OrderHistory.log_note(order=locked.order, note=note, user=user)
    return True


def _found(attempt: RefundAttempt) -> ProviderRefund | None:
    """The one refund the provider holds for *attempt*, or ``None`` when
    it cannot say (an unreadable provider, none found, several)."""
    charge = attempt.charge
    if charge is None or is_cash_collection(charge):
        return None
    try:
        provider = get_payment_provider(charge.provider)
    except ImproperlyConfigured, ValueError:
        return None
    candidates = provider.find_refunds(
        attempt.order,
        charge.provider_reference,
        attempt.amount,
        idempotency_key=attempt.provider_key,
    )
    if candidates is None:
        return None
    if not provider.refunds_carry_attempt:
        # Nothing ties these refunds to an attempt: one the ledger already
        # holds was made for an attempt that settled.
        booked = set(
            PaymentTransaction.objects.filter(
                kind=PaymentTransactionKind.REFUND,
                provider=charge.provider,
                provider_reference__in=[c.refund_id for c in candidates],
            ).values_list("provider_reference", flat=True)
        )
        candidates = [c for c in candidates if c.refund_id not in booked]
    if len(candidates) != 1 or candidates[0].amount != attempt.amount:
        return None
    return candidates[0]


def reconcile(attempt: RefundAttempt) -> Resolution:
    """Ask the provider what became of *attempt* and settle or decline it
    when the answer is unambiguous. An attempt left undecided is stamped
    ``looked_up_at``: from then on staff are told and may record it."""
    refund = _found(attempt)
    if refund is None:
        RefundAttempt.objects.filter(
            pk=attempt.pk, status=RefundAttemptStatus.PENDING
        ).update(looked_up_at=timezone.now())
        return Resolution.UNRESOLVED
    charge = attempt.charge
    if refund.made:
        OrderService.book_share(
            attempt.order,
            attempt,
            refund.amount,
            refund.refund_id,
            attempt.reason,
        )
        OrderHistory.log_note(
            order=attempt.order,
            note=(
                f"Refund via {charge.provider} found at the provider and "
                f"recorded: {refund.refund_id}"
            ),
        )
        return Resolution.SETTLED
    declined = _decline(
        attempt,
        f"Refund via {charge.provider} failed at the provider "
        f"({refund.refund_id}): nothing was returned.",
    )
    return Resolution.DECLINED if declined else Resolution.UNRESOLVED


def reconcile_overdue() -> dict[str, int]:
    """:func:`reconcile` every overdue attempt of the current store. One
    that fails does not stop the others."""
    counts = dict.fromkeys(Resolution, 0)
    attempts = RefundAttempt.objects.overdue().select_related("order", "charge")
    for attempt in attempts:
        try:
            counts[reconcile(attempt)] += 1
        except Exception:
            logger.exception(
                "Refund attempt %s could not be reconciled", attempt.pk
            )
            counts[Resolution.UNRESOLVED] += 1
    return {resolution.value: n for resolution, n in counts.items()}


def record_outcome(
    attempt: RefundAttempt,
    *,
    user,
    settled: bool,
    reference: str = "",
) -> None:
    """Staff say what became of an overdue *attempt*: the refund was made
    (*reference* is the provider's id of it, or how the cash was paid back)
    or it was not.

    Refused unless the attempt is still PENDING, overdue, and the
    automatic lookup could not decide it (before that the provider may
    still answer), and a reference already in the ledger
    cannot be claimed a second time. Goes through the same paths as an
    automatic resolution and leaves the user in the order's history.
    """
    reference = reference.strip()
    if not attempt.is_awaiting_staff:
        raise PaymentError(
            _(
                "The payment provider has not been asked about this refund "
                "yet: it is looked up automatically first."
            )
        )
    if settled:
        _record_settled(attempt, user, reference)
        return
    if not _decline(
        attempt,
        f"Refund of {attempt.amount} recorded as not made by "
        f"{user.get_username()}.",
        user,
    ):
        raise PaymentError(_("This refund has already been resolved."))


def _confirmed(attempt: RefundAttempt, reference: str) -> ProviderRefund:
    """The provider's own refund for *reference*, or a refusal saying why
    it cannot be booked."""
    charge = attempt.charge
    try:
        provider = get_payment_provider(charge.provider)
    except ImproperlyConfigured, ValueError:
        raise PaymentError(
            _("The payment provider cannot be reached to check this reference.")
        ) from None
    refund = provider.confirm_refund(
        attempt.order, charge.provider_reference, attempt.amount, reference
    )
    if refund is None:
        raise PaymentError(
            _(
                "The payment provider holds no refund with this reference "
                "for this payment and amount, or could not be read. Check "
                "the reference in the provider's dashboard."
            )
        )
    if not refund.made:
        raise PaymentError(
            _(
                "The payment provider reports this refund failed. Record "
                "the refund as not made instead."
            )
        )
    return refund


def _record_settled(attempt: RefundAttempt, user, reference: str) -> None:
    charge = attempt.charge
    if not reference:
        raise PaymentError(
            _(
                "Enter the provider's reference of the refund, or, for cash "
                "paid back, how it was paid back."
            )
        )
    cash = is_cash_collection(charge)
    if not cash:
        # What staff typed is never booked unchecked: the provider must
        # hold this refund, for this payment and this amount, and have made
        # it. It is booked as the provider spells it, the key the
        # provider's own webhook books under.
        confirmed = _confirmed(attempt, reference)
        reference = confirmed.refund_id
    taken = (
        PaymentLedger.has_cod_refund(attempt.order, charge, reference)
        if cash
        else PaymentTransaction.objects.filter(
            kind=PaymentTransactionKind.REFUND,
            provider=charge.provider,
            provider_reference__iexact=reference,
        ).exists()
    )
    if taken:
        raise PaymentError(
            _("A refund with this reference is already recorded.")
        )
    with transaction.atomic():
        locked = _lock(attempt)
        if locked is None:
            raise PaymentError(_("This refund has already been resolved."))
        paid: Money = locked.amount
        OrderService.book_share(
            locked.order,
            locked,
            paid,
            None if cash else reference,
            locked.reason,
            reference if cash else "",
        )
        OrderHistory.log_note(
            order=locked.order,
            note=(
                f"Refund of {paid} recorded as made ({reference}) by "
                f"{user.get_username()}."
            ),
            user=user,
        )
