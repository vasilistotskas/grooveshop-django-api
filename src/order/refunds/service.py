"""Refunds: claiming what a refund returns, allocating it over the
payments, making each provider share, booking it and declining it.

``RefundService`` is the one place a refund is made; ``refund_order`` is
its entry point (the staff action, the order-item refund view and
``OrderService``'s cancellation call it).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise
from djmoney.money import Money

from order.accounting import credit_allocation
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import PaymentStatus
from order.exceptions import (
    DisputeOpenError,
    PaymentError,
)
from order.models.item import OrderItem
from order.models.order import (
    Order,
)
from order.models.refund_attempt import (
    RefundAttempt,
    RefundAttemptKind,
    RefundAttemptStatus,
    RefundReason,
)
from order.payments.payment import (
    provider_supports,
    registered_provider_codes,
)
from order.payments.payment_ledger import (
    REPAYMENT_REFERENCE_MAX_LENGTH,
    PaymentLedger,
    is_cash_collection,
)
from order.refunds.refund_lines import plan_pieces
from order.signals import (
    order_refunded,
    order_stored_value_returned,
)

logger = logging.getLogger(__name__)

# The payment statuses of an order money was taken for, whether or not it
# has been paid back since. A refund looks for a payment the ledger lacks
# only on one of these: an unpaid order has none to find.
_PAID_PAYMENT_STATUSES: frozenset[str] = frozenset(
    {
        PaymentStatus.COMPLETED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    }
)


@dataclass(frozen=True, slots=True)
class RefundResult:
    """What one ``RefundService.refund_order`` call paid back.

    A refund can be split across the payments of an order, one provider
    call each, and a later one can be declined after an earlier one went
    through. ``refunded`` is therefore the money that actually moved (and
    is in the ledger), never a yes/no: a caller that only learns "failed"
    would tell staff nothing was refunded when part of it was.

    ``declined`` is the provider's answer for the share it did not make,
    ``None`` when every share was. Its ``error`` can carry raw provider
    text, so it is for logs and staff screens, never for a customer.
    """

    refunded: Money
    refund_ids: tuple[str, ...] = ()
    declined: dict[str, Any] | None = None
    #: What went back onto gift cards and points (no provider moved it).
    stored: Money | None = None

    @property
    def complete(self) -> bool:
        """Every share of the refund was made."""
        return self.declined is None

    @property
    def value_returned(self) -> Money:
        """Everything that went back: the provider's money and the value
        returned onto gift cards and points."""
        return self.refunded + (self.stored or Money(0, self.refunded.currency))

    @property
    def paid_something(self) -> bool:
        return self.value_returned.amount > 0


@dataclass(frozen=True, slots=True)
class _SharePaid:
    """A share of a refund the provider made and the ledger holds."""

    attempt_pk: int
    paid_back: Money
    refund_id: str


@dataclass(frozen=True, slots=True)
class _StoredReturned:
    """The share of a refund that went back onto gift cards and points."""

    attempt_pk: int
    amount: Money


@dataclass(frozen=True, slots=True)
class _ShareDeclined:
    """A share of a refund that was not made: the provider's answer."""

    attempt_pk: int
    declined: dict[str, Any]


class RefundService:
    @staticmethod
    def _note_refund_declined(
        order: Order, provider_code: str, response: dict[str, Any]
    ) -> None:
        """Leave the reason a refund was not made where staff look.

        Nothing is recorded for it, and a caller may only log the outcome
        (the admin cancel action once dropped it altogether), so the reason
        goes in the order's history. A refund whose outcome is unknown (the
        provider did not answer) says so: it may have been made, and its
        webhook records it. ``NOTE`` rows are not customer-visible
        (``CUSTOMER_VISIBLE_HISTORY_TYPES``), which matters because the
        provider's own wording goes in."""
        from order.models.history import OrderHistory

        details = response.get("details")
        outcome = (
            "has an unknown outcome (it may have been made; the provider's "
            "webhook records it, check its dashboard before trying again)"
            if response.get("outcome_unknown")
            else "was not processed"
        )
        OrderHistory.log_note(
            order=order,
            note=(
                f"Refund via {provider_code} {outcome}: "
                f"{response.get('error')}"
                + (f" ({details})" if details else "")
            ),
        )

    @staticmethod
    def _nothing_to_refund(order: Order, payment_status: str) -> StrOrPromise:
        """Why *order*, whose payment status is *payment_status*, has no
        refundable payment."""
        if (
            PaymentLedger.captured(order)
            or PaymentLedger.stored_value(order).returned > 0
        ):
            return _("This order has already been refunded.")
        if payment_status in _PAID_PAYMENT_STATUSES:
            return _(
                "This order was not paid through a payment provider, so "
                "there is nothing to refund here."
            )
        if order.pay_way and order.pay_way.is_collected_on_delivery:
            return _(
                "The payment on delivery has not been collected yet, so "
                "there is nothing to refund."
            )
        return _("This order has not been paid yet.")

    @classmethod
    def refund_order(
        cls,
        order: Order,
        amount: Money | None = None,
        reason: str = "",
        *,
        repayment_reference: str = "",
        lines: Sequence[tuple[OrderItem, int]] = (),
        include_shipping: bool = False,
        reason_code: str = "",
        retry_request_id: UUID | None = None,
    ) -> RefundResult:
        """Refund ``amount`` of what *order* paid, or everything still
        refundable when ``amount`` is ``None``.

        **By lines.** With ``lines`` (``(item, units)`` pairs) and/or
        ``include_shipping`` the amount is derived from the order's frozen
        figures (``order.refunds.refund_lines``: price, ``discount_allocation`` and
        ``vat_rate`` of the units, the shipping split over the rates as the
        receipt splits it) and ``amount`` must not be given. No line can be
        refunded past what was sold less what earlier line refunds paid
        back. Each share stores the lines it pays for as its own
        ``CreditAllocation`` rows (``_allocate_lines_claim``), so its credit
        document (myDATA 11.4) carries exactly their VAT rates. A bare ``amount`` is a goodwill
        refund and is credited in proportion over the receipt's rates.
        ``reason_code`` is a ``RefundReason`` and ``reason`` the note that
        goes with it. A request by lines is always planned fresh; the only
        way to resume one is ``retry_request_id``, the ``request_id`` of a
        refund by lines with live PENDING shares (a provider call whose
        outcome is unknown): those shares are called again under their own
        idempotency keys, and nothing else is claimed.

        A payment a provider captured is refunded through that provider.
        Cash a courier collected on delivery (a ``COD_COLLECTION`` row) has
        no provider to refund it: the merchant pays the customer back
        themselves, and ``repayment_reference`` (a bank transfer id, say) is
        how they say so. It is required exactly when the refund touches
        such cash, checked before any provider is called, and is the
        idempotency key of that share: the same reference cannot book the
        same cash twice. Each share, either way, is one REFUND row and one
        ``order_refunded``, so the credit note and everything else that
        listens run as they do for a provider refund.

        Three steps, so that no database transaction is open while a
        provider answers (a connection idle in a transaction is killed
        after ten seconds, and the refund would be made at the provider
        and lost here):

        1. **Claim**, under the order lock and committed: what can be
           refunded is read from the payment ledger (received minus
           refunded, payment by payment, less what live attempts hold), the
           request is checked against it (G0280: never past it), and each
           share becomes a ``RefundAttempt`` whose amount is reserved and
           whose key the provider receives.
        2. **Call** the provider, with no transaction open.
        3. **Record**, under the lock again: the refund the provider
           accepted becomes a ``PaymentTransaction`` and ``payment_status``
           follows from the ledger's sums (``PaymentLedger.record_refund``).

        A request that matches an attempt still pending (a double click, a
        retry after a crash or a timeout) reuses it, key included, so a
        provider that honours keys returns the refund it already made. A
        provider that does not (Viva) is never called a second time for it:
        the answer is lost, and its webhook books the refund.

        Returns what was paid back. A refund split across payments can be
        declined part-way: the shares already made stay recorded and are
        reported in ``refunded``, and ``declined`` holds the provider's
        answer for the one that was not (``RefundResult``). A share whose
        outcome is unknown (a timeout) is reported declined with
        ``outcome_unknown``; its attempt stays pending until the provider's
        webhook or a retry settles it.
        """
        reference = repayment_reference.strip()
        reason = cls.reason_text(reason_code, reason)
        attempts, declined = cls._claim_refund(
            order,
            amount,
            reason,
            reference,
            requested=cls._requested_units(lines),
            include_shipping=include_shipping,
            reason_code=reason_code,
            retry_request_id=retry_request_id,
        )
        if declined is not None:
            return RefundResult(
                refunded=Money(0, order.amount_due.currency),
                declined=declined,
            )

        refunded = stored = Money(0, attempts[0].amount.currency)
        refund_ids: list[str] = []
        declined = None
        for attempt in attempts:
            share = cls._make_refund(order, attempt, reason, reference)
            if isinstance(share, _ShareDeclined):
                declined = share.declined
                break
            if isinstance(share, _StoredReturned):
                stored += share.amount
                continue
            refunded += share.paid_back
            refund_ids.append(share.refund_id)
        if isinstance(share, _ShareDeclined):
            # What the earlier shares paid back is recorded and stays so;
            # the later ones were never called, so they hold nothing back.
            RefundAttempt.objects.filter(
                pk__in=[a.pk for a in attempts],
                status=RefundAttemptStatus.PENDING,
                refund__isnull=True,
            ).exclude(pk=share.attempt_pk).update(
                status=RefundAttemptStatus.DECLINED
            )

        return RefundResult(
            refunded=refunded,
            refund_ids=tuple(refund_ids),
            declined=declined,
            stored=stored,
        )

    @staticmethod
    def reason_text(reason_code: str, note: str) -> str:
        """The reason as it is written on the ledger and in the history:
        the category's label, then the note."""
        note = note.strip()
        if not reason_code:
            return note
        label = str(RefundReason(reason_code).label)
        return f"{label}: {note}" if note else label

    @staticmethod
    def _requested_units(
        lines: Sequence[tuple[OrderItem, int]],
    ) -> dict[int, int]:
        """Order line pk to the units asked for (a line named twice adds)."""
        requested: dict[int, int] = {}
        for item, units in lines:
            requested[item.pk] = requested.get(item.pk, 0) + units
        return requested

    @classmethod
    @transaction.atomic
    def _claim_refund(
        cls,
        order: Order,
        amount: Money | None,
        reason: str,
        reference: str,
        *,
        requested: Mapping[int, int],
        include_shipping: bool,
        reason_code: str,
        retry_request_id: UUID | None,
    ) -> tuple[list[RefundAttempt], dict[str, Any] | None]:
        """Step 1 of :meth:`refund_order`: the attempts to make, or the
        provider-less refusal to report as declined."""
        from django.core.exceptions import ImproperlyConfigured

        # Lock before anything is read from the ledger: the remainder, the
        # attempts that hold part of it and the rows written below are one
        # step. The status is read under the lock too, not taken from the
        # caller's possibly stale ``order``.
        locked = (
            Order.objects.select_for_update()
            .values("payment_status", "metadata")
            .get(pk=order.pk)
        )
        payment_status = locked["payment_status"]
        # Every older source nobody allocated yet is decided before this
        # claim takes its room (``order.accounting.credit_allocation``).
        credit_allocation.allocate_order(order)

        by_lines = bool(requested) or include_shipping
        if by_lines and amount is not None:
            raise PaymentError(
                _(
                    "A refund by lines is worth what its lines are worth: "
                    "do not give an amount with them."
                )
            )
        if amount is not None and amount.amount <= 0:
            raise PaymentError(
                _("The refund amount must be greater than zero.")
            )

        if (
            not PaymentLedger.captured(order)
            and payment_status in _PAID_PAYMENT_STATUSES
        ):
            provider_code = order.pay_way.provider_code if order.pay_way else ""
            if (
                order.payment_id
                and provider_code in registered_provider_codes()
            ):
                try:
                    charge = PaymentLedger.ensure_charge(
                        order,
                        provider_code=provider_code,
                        payment_id=order.payment_id,
                    )
                except ImproperlyConfigured as exc:
                    raise PaymentError(
                        _(
                            "This store has no credentials for the payment "
                            "provider, so the payment cannot be refunded "
                            "here."
                        )
                    ) from exc
                if charge is None:
                    declined = {
                        "error": (
                            "Could not read the amount the provider captured "
                            "for this payment."
                        )
                    }
                    cls._note_refund_declined(order, provider_code, declined)
                    return [], declined

        if PaymentLedger.dispute_open(order, locked["metadata"]):
            raise DisputeOpenError(
                _(
                    "A dispute is open on this payment and the payment "
                    "provider does not accept a refund until it is decided. "
                    "Refund once it has closed."
                )
            )

        # A refund by lines is always planned fresh from what its lines
        # have had back (``held_values``): a share that was declined holds
        # nothing, so a new request covers what is left at the current
        # charges. Only the retry of a named pending request resumes
        # attempts, and only a bare amount is matched against live ones.
        if retry_request_id is not None:
            if by_lines or amount is not None:
                raise PaymentError(
                    _(
                        "A retry repeats exactly what was asked: give no "
                        "lines, shipping or amount with it."
                    )
                )
            return cls._pending_request_shares(order, retry_request_id), None
        resumed = [] if by_lines else cls._attempts_to_resume(order, amount)
        if resumed:
            cls._require_resumable(resumed)
            return resumed, None

        pieces: list[credit_allocation.Piece] = []
        if by_lines:
            pieces = plan_pieces(
                order, requested, include_shipping=include_shipping, lock=True
            )
            amount = Money(
                sum((piece.amount for piece in pieces), Decimal(0)),
                order.amount_due.currency,
            )

        # The value that can still be refunded: the provider's money, then
        # what the order paid with gift cards and points.
        provider_refundable = PaymentLedger.refundable(order)
        stored_refundable = PaymentLedger.stored_value_refundable(order)
        refundable = provider_refundable + stored_refundable
        if refundable <= 0:
            raise PaymentError(cls._nothing_to_refund(order, payment_status))
        if amount is not None and amount.amount > refundable:
            raise PaymentError(
                _(
                    "Refund amount ({refund}) cannot exceed the refundable "
                    "amount ({refundable})."
                ).format(refund=amount.amount, refundable=refundable)
            )

        # The provider is paid back first; only the excess goes back onto
        # gift cards and points (and in proportion to what each gave).
        currency = order.amount_due.currency
        value = refundable if amount is None else amount.amount
        provider_part = min(value, provider_refundable)
        stored_part = value - provider_part
        plan = (
            PaymentLedger.plan_refund(order, Money(provider_part, currency))
            if provider_part > 0
            else []
        )

        # The cash shares need the merchant's word on how they were paid
        # back, and may not repeat a reference already booked. Both are
        # settled before the first provider call, so a refusal never leaves
        # a mixed refund half made.
        cash = [c for c, _share in plan if is_cash_collection(c)]
        if cash:
            if not reference:
                raise PaymentError(
                    _(
                        "Say how the cash was paid back (a bank transfer id, "
                        "for example): part of this refund returns money "
                        "collected on delivery."
                    )
                )
            if len(reference) > REPAYMENT_REFERENCE_MAX_LENGTH:
                raise PaymentError(
                    _(
                        "The repayment reference can be at most "
                        "{max_length} characters."
                    ).format(max_length=REPAYMENT_REFERENCE_MAX_LENGTH)
                )
            if any(
                PaymentLedger.has_cod_refund(order, collection, reference)
                for collection in cash
            ):
                raise PaymentError(
                    _(
                        "A refund with this reference is already recorded "
                        "for this order."
                    )
                )

        if credit_allocation.undecided(order):
            # An older refund still waits for a claim in flight to resolve
            # (``credit_allocation.allocate_order``): this claim must not
            # take the room it needs.
            raise RefundService._waiting_error(order)
        request_id = uuid.uuid4() if by_lines else None
        attempts = [
            RefundAttempt.objects.create(
                order=order,
                charge=charge,
                amount=share,
                reason=reason[:255],
                reason_code=reason_code,
                request_id=request_id,
            )
            for charge, share in plan
        ]
        if stored_part > 0:
            attempts.append(
                RefundAttempt.objects.create(
                    order=order,
                    kind=RefundAttemptKind.STORED_VALUE,
                    charge=None,
                    amount=Money(stored_part, currency),
                    reason=reason[:255],
                    reason_code=reason_code,
                    request_id=request_id,
                )
            )
        cls._allocate_claim(order, attempts, pieces)
        return attempts, None

    @staticmethod
    def _waiting_error(
        order: Order, claims: Sequence[RefundAttempt] | None = None
    ) -> PaymentError:
        """The refusal of a refund that must wait for claims still PENDING
        (*claims*, or those that keep a source of the order undecided)."""
        return PaymentError(
            _(
                "Another refund of this order is waiting for "
                "%(blockers)s to be settled, so nothing more can be "
                "refunded until then."
            )
            % {"blockers": credit_allocation.describe_blockers(order, claims)}
        )

    @staticmethod
    def _allocate_claim(
        order: Order,
        attempts: Sequence[RefundAttempt],
        pieces: Sequence[credit_allocation.Piece] = (),
    ) -> None:
        """Fix, now and under the order lock, where the refund sits on the
        receipt's VAT rates: its amount spread over the headroom left at
        this moment (``order.accounting.credit_allocation``), laid over its shares in
        order. A refund the headroom cannot hold is refused before any
        provider is called: its credit document could never be issued.

        A refund by lines (*pieces*) writes its own pieces instead
        (:meth:`_allocate_lines_claim`)."""
        if pieces:
            RefundService._allocate_lines_claim(order, attempts, pieces)
            return
        if not credit_allocation.has_rates(order):
            # Nothing taxable: nothing is owed, and each claim is marked
            # allocated so the backfill never picks it up.
            for attempt in attempts:
                credit_allocation.store(
                    order, [], nothing_owed=True, refund_attempt=attempt
                )
            return
        # Points are a discount, not money returned: their share owes no
        # credit document and takes no room on the receipt. A gift card is
        # payment the receipt holds.
        stored = PaymentLedger.stored_value(order)
        owed = [
            PaymentLedger.split_stored_value(stored, a.amount.amount)[0]
            if a.kind == RefundAttemptKind.STORED_VALUE
            else a.amount.amount
            for a in attempts
        ]
        # A duplicate payment is paid back first, and giving it back is not
        # reversing the sale: that part owes no credit and takes no room.
        ids = [a.pk for a in attempts]
        excess = PaymentLedger.refundable_excess(order, exclude=ids)
        provider_amount = sum(
            (
                a.amount.amount
                for a in attempts
                if a.kind == RefundAttemptKind.PROVIDER
            ),
            Decimal(0),
        )
        if provider_amount > 0 and min(
            PaymentLedger.refundable_excess(
                order, exclude=ids, claims_hold=False
            ),
            provider_amount,
        ) > min(excess, provider_amount):
            # A claim still PENDING holds part of the duplicate payment
            # this refund would take: what it owes depends on whether that
            # claim is settled or declined, which nobody knows yet. Decide
            # nothing on a guess: wait, as a refund booked without a claim
            # does.
            raise RefundService._waiting_error(
                order,
                credit_allocation.excess_holders(
                    order, provider_amount, exclude=ids
                ),
            )
        for i, attempt in enumerate(attempts):
            if attempt.kind != RefundAttemptKind.PROVIDER or excess <= 0:
                continue
            taken = min(excess, owed[i])
            owed[i] -= taken
            excess -= taken
        total = sum(owed, Decimal(0))
        allocation = (
            credit_allocation.allocate_pro_rata(order, total)
            if total > 0
            else {}
        )
        held = sum(allocation.values(), Decimal(0))
        if held < total:
            if blockers := credit_allocation.room_blockers(
                order,
                lambda **held_by: (
                    sum(
                        credit_allocation.allocate_pro_rata(
                            order, total, **held_by
                        ).values(),
                        Decimal(0),
                    )
                    < total
                ),
                exclude=ids,
            ):
                raise RefundService._waiting_error(order, blockers)
            raise PaymentError(
                _(
                    "The receipt of this order holds only %(left)s more "
                    "across its VAT rates, less than the %(amount)s to "
                    "refund, so its credit document could not be issued "
                    "and nothing was refunded."
                )
                % {"left": held, "amount": total}
            )
        per_share = credit_allocation.split_over_shares(
            credit_allocation.pieces_of(allocation), owed
        )
        for attempt, share_pieces in zip(attempts, per_share, strict=True):
            credit_allocation.store(
                order,
                share_pieces,
                nothing_owed=not share_pieces,
                refund_attempt=attempt,
            )

    @staticmethod
    def _allocate_lines_claim(
        order: Order,
        attempts: Sequence[RefundAttempt],
        pieces: Sequence[credit_allocation.Piece],
    ) -> None:
        """Store a refund by lines as what it is: the lines' own pieces
        (order line, kind, VAT rate, units, exact gross from the frozen
        figures), laid over its shares in order (``split_over_shares``), so
        its credit document carries exactly their rates and what a line has
        had back is read from these rows (``order.refunds.refund_lines``).

        The lines are goods the customer returned, owed as credit at their
        own rates in full, so a duplicate payment (``refundable_excess``) is
        never taken out of them: the share is all goods
        (``goods_claimed`` equals its amount) and the duplicate payment
        stays refundable as a bare amount. Points are a discount and owe no
        credit: a stored-value share keeps only the gift card part of its
        pieces, in proportion.

        Each rate must fit its headroom now: a rate the receipt no longer
        holds is refused before any provider is called, as a bare amount
        the headroom cannot hold is."""
        stored = PaymentLedger.stored_value(order)
        per_share = credit_allocation.split_over_shares(
            pieces, [a.amount.amount for a in attempts]
        )
        kept = [
            credit_allocation.scale_pieces(
                share_pieces,
                PaymentLedger.split_stored_value(stored, a.amount.amount)[0],
            )
            if a.kind == RefundAttemptKind.STORED_VALUE
            else share_pieces
            for a, share_pieces in zip(attempts, per_share, strict=True)
        ]
        wanted = credit_allocation.by_rate(
            piece for share_pieces in kept for piece in share_pieces
        )
        short = credit_allocation.first_short_rate(order, wanted)
        if short is not None:
            # A claim still PENDING may hold the room: the refusal is then
            # "wait", never the final "the receipt holds only".
            if blockers := credit_allocation.room_blockers(
                order,
                lambda **held: (
                    credit_allocation.first_short_rate(order, wanted, **held)
                    is not None
                ),
                exclude=[a.pk for a in attempts],
            ):
                raise RefundService._waiting_error(order, blockers)
            rate, left = short
            raise PaymentError(
                _(
                    "The receipt of this order holds only %(left)s more at "
                    "%(rate)s%% VAT, less than these lines are worth "
                    "(%(amount)s), so their credit document could not be "
                    "issued and nothing was refunded. A goodwill refund of "
                    "an amount can still be made."
                )
                % {"left": left, "rate": rate, "amount": wanted[rate]}
            )
        for attempt, share_pieces in zip(attempts, kept, strict=True):
            credit_allocation.store(
                order,
                share_pieces,
                nothing_owed=not share_pieces,
                refund_attempt=attempt,
            )

    @staticmethod
    def _resumable(order: Order):
        """The live attempts of *order* a repeated request may resume, oldest
        first. Cash shares are never resumed: their reference is their own
        idempotency key, and the shares of a refund by lines (``request_id``
        is set) are never matched by amount: only a named retry resumes
        them."""
        return (
            RefundAttempt.objects.live()
            .select_related("charge")
            .filter(order=order, request_id__isnull=True)
            .exclude(
                charge__kind__in=(
                    PaymentTransactionKind.COD_COLLECTION,
                    PaymentTransactionKind.OFFLINE_COLLECTION,
                )
            )
            .order_by("created_at", "id")
        )

    @classmethod
    def resumes_a_refund(
        cls,
        order: Order,
        amount: Money | None,
        *,
        retry_request_id: UUID | None = None,
        by_lines: bool = False,
    ) -> bool:
        """Whether a refund would resume live attempts rather than make a
        new refund (read without the lock, and answering as the locked path
        does): the retry of a named request by lines resumes its live shares
        (:meth:`_pending_request_shares`), a bare *amount* matches the
        attempts of :meth:`_attempts_to_resume`, and a request *by_lines*
        that is no retry is always planned fresh, never a resume. The refund
        dialog asks nothing a resume does not need, such as how cash was
        paid back."""
        if retry_request_id is not None:
            return (
                RefundAttempt.objects.live()
                .filter(order=order, request_id=retry_request_id)
                .exists()
            )
        if by_lines:
            return False
        return bool(
            cls._match_resumable(order, list(cls._resumable(order)), amount)
        )

    @classmethod
    def _attempts_to_resume(
        cls, order: Order, amount: Money | None
    ) -> list[RefundAttempt]:
        """The live attempts a request is a repeat of: the one for the same
        amount, or all of them when together they are the amount, or, for a
        refund of everything, all of them once they hold everything that was
        left."""
        pending = list(cls._resumable(order).select_for_update(of=("self",)))
        return cls._match_resumable(order, pending, amount)

    @staticmethod
    def _match_resumable(
        order: Order, pending: list[RefundAttempt], amount: Money | None
    ) -> list[RefundAttempt]:
        """Which of *pending* a request for *amount* repeats (see
        :meth:`_attempts_to_resume`)."""
        if not pending:
            return []
        if amount is None:
            return pending if PaymentLedger.refundable_value(order) <= 0 else []
        match = next(
            (a for a in pending if a.amount.amount == amount.amount), None
        )
        if match:
            return [match]
        together = sum((a.amount.amount for a in pending), Decimal(0))
        return pending if together == amount.amount else []

    @classmethod
    def _pending_request_shares(
        cls, order: Order, request_id: UUID
    ) -> list[RefundAttempt]:
        """The live PENDING shares of one refund by lines, provider shares
        first, for the retry the dialog names: nothing of any other request,
        and no share that was declined or settled."""
        shares = list(
            RefundAttempt.objects.live()
            .select_related("charge")
            .select_for_update(of=("self",))
            .filter(order=order, request_id=request_id)
            .order_by("created_at", "id")
        )
        if not shares:
            raise PaymentError(
                _("There is no pending refund of this order to retry.")
            )
        cls._require_resumable(shares)
        return sorted(
            shares,
            key=lambda a: (a.kind == RefundAttemptKind.STORED_VALUE, a.pk),
        )

    @staticmethod
    def unresumable_reason(shares: Iterable[RefundAttempt]) -> str | None:
        """Why *shares* may not be called again, or ``None`` when they may.
        Shares the provider may already have made are only called again
        under their own key, and only while that is safe: the provider must
        remember keys, and the attempt must not be overdue (the provider may
        have forgotten the key by then, and the refund is looked up by the
        reconciliation or recorded by staff instead). A cash share has no
        provider to ask: its reference is its own key, and staff record how
        it was paid back. The one rule of the locked path
        (:meth:`_require_resumable`) and of what the dialog offers. Only
        shares still PENDING are judged: a settled or declined share of
        the request is never called again, whatever its kind."""
        for attempt in shares:
            if attempt.status != RefundAttemptStatus.PENDING:
                continue
            charge = attempt.charge
            if charge is not None and is_cash_collection(charge):
                return str(
                    _(
                        "Part of this refund was paid back in cash and its "
                        "outcome is not recorded yet. Cash is never "
                        "repeated: record how it was paid back with Record "
                        "refund outcome on the order."
                    )
                )
            if charge is not None and not provider_supports(
                charge.provider, "idempotent_refunds"
            ):
                return str(
                    _(
                        "A refund of this amount was already sent to the "
                        "payment provider and its result is not known "
                        "yet. It is recorded when the provider confirms "
                        "it; check the provider's dashboard before "
                        "trying again."
                    )
                )
            if attempt.is_overdue:
                return str(
                    _(
                        "A refund of this amount was sent to the "
                        "payment provider a while ago and its result is "
                        "still not known. It is looked up automatically; "
                        "if it stays unknown, record its outcome with "
                        "Record refund outcome on the order before "
                        "trying again."
                    )
                )
        return None

    @classmethod
    def _require_resumable(cls, shares: Sequence[RefundAttempt]) -> None:
        """Refuse to call *shares* again when :meth:`unresumable_reason`
        says so."""
        if reason := cls.unresumable_reason(shares):
            raise PaymentError(reason)

    @classmethod
    def _make_refund(
        cls,
        order: Order,
        attempt: RefundAttempt,
        reason: str,
        reference: str,
    ) -> _SharePaid | _StoredReturned | _ShareDeclined:
        """Steps 2 and 3 of :meth:`refund_order` for one share."""
        from django.core.exceptions import ImproperlyConfigured

        from order.payments.payment import get_payment_provider

        if attempt.kind == RefundAttemptKind.STORED_VALUE:
            return cls._return_stored_value(order, attempt, reason)

        charge = attempt.charge
        refund_id: str | None
        if is_cash_collection(charge):
            paid_back = attempt.amount
            refund_id = None
        else:
            try:
                provider = get_payment_provider(charge.provider)
            except ImproperlyConfigured:
                return cls._decline(
                    order,
                    attempt,
                    charge.provider,
                    {
                        "error": (
                            f"No credentials for {charge.provider} on this "
                            "store."
                        )
                    },
                )
            # No transaction is open here: nothing is held while the
            # provider answers.
            response: dict[str, Any]
            success, response = provider.refund_payment(
                payment_id=charge.provider_reference,
                amount=attempt.amount,
                idempotency_key=attempt.provider_key,
            )
            if success and not response.get("refund_id"):
                # Money may have moved but nothing identifies it, so it
                # cannot be booked under a key the provider's own
                # notification would not repeat.
                return cls._decline(
                    order,
                    attempt,
                    charge.provider,
                    {
                        "error": "The provider accepted the refund without "
                        "naming it.",
                        "outcome_unknown": True,
                    },
                )
            if not success:
                return cls._decline(order, attempt, charge.provider, response)
            paid_back = Money(
                Decimal(str(response["amount"])), attempt.amount.currency
            )
            refund_id = response["refund_id"]

        return cls.book_share(
            order, attempt, paid_back, refund_id, reason, reference
        )

    @classmethod
    def book_share(
        cls,
        order: Order,
        attempt: RefundAttempt,
        paid_back: Money,
        refund_id: str | None,
        reason: str,
        reference: str = "",
    ) -> _SharePaid:
        """Step 3 of :meth:`refund_order` for one share, and the one place
        a refund the provider made becomes a ledger row: under the order
        lock, the refund is recorded (*refund_id* is the provider's id, or
        ``None`` for cash paid back by the merchant under *reference*), the
        attempt settles and ``order_refunded`` fires. Also what resolves an
        attempt whose outcome was unknown (``order.refunds.refund_reconciliation``)."""
        charge = attempt.charge
        with transaction.atomic():
            Order.objects.select_for_update().values_list("pk", flat=True).get(
                pk=order.pk
            )
            settled = RefundAttempt.objects.select_for_update().get(
                pk=attempt.pk
            )
            if settled.status == RefundAttemptStatus.SETTLED:
                # The provider's webhook booked this refund while the call
                # was in flight; booking it again would count it twice.
                return _SharePaid(
                    attempt.pk, paid_back, settled.refund.provider_reference
                )
            # Only a PENDING attempt is a claim to fulfil. One declined
            # while the provider was asked (staff marked it not made) is
            # never revived: if the money moved anyway it is booked as a
            # refund nobody claimed, which the allocation then decides.
            claim = (
                settled
                if settled.status == RefundAttemptStatus.PENDING
                else None
            )
            if refund_id is None:
                row, created = PaymentLedger.record_cod_refund(
                    order,
                    collection=charge,
                    amount=paid_back,
                    reference=reference,
                    description=reason,
                    attempt=claim,
                )
                refund_id = row.provider_reference
            else:
                row, created = PaymentLedger.record_refund(
                    order,
                    charge=charge,
                    amount=paid_back,
                    reference=refund_id,
                    description=reason,
                    attempt=claim,
                    match_attempt=claim is not None,
                )
            if created:
                # One signal per refund the ledger took, so a failure on a
                # later payment leaves nothing unannounced.
                order_refunded.send(
                    sender=cls,
                    order=order,
                    amount=paid_back,
                    reason=reason,
                    refund=row,
                )
                logger.info("Order %s refunded %s", order.id, paid_back)
        return _SharePaid(attempt.pk, paid_back, refund_id)

    @classmethod
    def _return_stored_value(
        cls, order: Order, attempt: RefundAttempt, reason: str
    ) -> _StoredReturned:
        """Step 3 of :meth:`refund_order` for the share that goes back onto
        gift cards and points: no provider is asked, so it is one
        transaction under the order lock.

        The amount is split between the cards and the points in proportion
        to what each has not yet got back, so both are used up together and
        the last refund returns exactly what is left (no cent or point is
        lost to rounding). Gift cards are credited first, then the points,
        both read under their own locks, so a cancel racing this refund
        cannot return the same value twice; what is owed is what the
        ledgers say, not the amount claimed.
        """
        from giftcard.services import GiftCardService
        from loyalty.services import LoyaltyService

        with transaction.atomic():
            Order.objects.select_for_update().values_list("pk", flat=True).get(
                pk=order.pk
            )
            settled = RefundAttempt.objects.select_for_update().get(
                pk=attempt.pk
            )
            if settled.status == RefundAttemptStatus.SETTLED:
                return _StoredReturned(
                    attempt.pk, PaymentLedger.stored_returned_by(order, settled)
                )

            gift_card_part, points_part = PaymentLedger.split_stored_value(
                PaymentLedger.stored_value(order), attempt.amount.amount
            )
            gift_card = (
                GiftCardService.credit_refund(
                    order, gift_card_part, refund_attempt=settled
                )
                if gift_card_part > 0
                else Decimal(0)
            )
            points = LoyaltyService.restore_spent_points(
                order, points_part, refund_attempt=settled
            )
            settled.status = RefundAttemptStatus.SETTLED
            settled.save(update_fields=["status"])

            previous_status = order.payment_status
            order.payment_status = PaymentLedger.refund_status(order)
            order.save(update_fields=["payment_status"])
            returned = PaymentLedger.stored_returned_by(order, settled)
            # Only the gift cards are payment the receipt holds: points are
            # a discount and owe no credit document.
            credit_allocation.scale(gift_card, refund_attempt=settled)
            credit_allocation.scale_points(
                Decimal(returned.amount) - gift_card, refund_attempt=settled
            )
            credit_allocation.mark_moved(timezone.now(), refund_attempt=settled)
            order_stored_value_returned.send(
                sender=cls,
                order=order,
                amount=returned,
                gift_card=Money(gift_card, attempt.amount.currency),
                points=points,
                reason=reason,
                previous_payment_status=previous_status,
            )
            logger.info(
                "Order %s: %s back onto gift cards, %s points restored",
                order.id,
                gift_card,
                points,
            )
        return _StoredReturned(attempt.pk, returned)

    @classmethod
    def _decline(
        cls,
        order: Order,
        attempt: RefundAttempt,
        provider_code: str,
        response: dict[str, Any],
    ) -> _ShareDeclined:
        """The provider did not refund this share. A refusal frees what the
        attempt held; an unknown outcome (a timeout) keeps it held, because
        the refund may have been made."""
        if not response.get("outcome_unknown"):
            RefundAttempt.objects.filter(
                pk=attempt.pk, status=RefundAttemptStatus.PENDING
            ).update(status=RefundAttemptStatus.DECLINED)
        cls._note_refund_declined(order, provider_code, response)
        return _ShareDeclined(attempt.pk, response)
