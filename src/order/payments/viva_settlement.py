"""Settling a Viva payment: the code a Transaction Payment Created webhook
(``EventTypeId`` 1796) runs, and the only code that marks an order paid by
Viva.

Two things reach it. The webhook (``order.views.viva_webhook``) with the
transaction Viva announced, and :func:`settle_landed_payment` with each
transaction Viva lists as paid for one of the order's payment orders, when
the webhook may never have landed: before ``auto_cancel_stuck_pending_orders``
releases an unpaid order and when the buyer comes back from the hosted page
(``viva_return``). Both go through :func:`apply_event`, so the transaction
is verified the same way, the same ledger row and status are written, and
the ``VivaWebhookEvent`` row of the transaction makes whichever arrives
second a no-op.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django.db import transaction

from order.enum.landed_payment import LandedPayment
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentStatusUnavailableError
from order.models.history import OrderHistory
from order.models.order import Order
from order.payments import payment_hold
from order.payments.payment import (
    LandedCharge,
    order_viva_codes,
    provider_amount,
)
from tenant.celery import dispatch_on_commit

logger = logging.getLogger(__name__)

#: Viva ``EventTypeId`` of Transaction Payment Created.
VIVA_TRANSACTION_PAYMENT_CREATED = 1796

#: What handles one event for a locked order: ``(order, event_data,
#: transaction_id, **read)``, answering the ``VivaWebhookEvent`` outcome to
#: record (``None`` is a processed event). ``read`` is what the event's
#: :data:`EventRead` took from Viva before the lock.
EventHandler = Callable[..., str | None]

#: What an event reads from Viva before its order row is locked: ``(order,
#: event_data, transaction_id)``, answering the keyword arguments its
#: handler judges under the lock. Retrieve Transaction is an HTTP call,
#: and the row is never held across one. Raises ``RuntimeError`` when Viva
#: cannot be read.
EventRead = Callable[[Order, dict[str, Any], str], dict[str, Any]]


def transaction_hash(transaction_id: object) -> str:
    """A short, stable name for a transaction on the webhook's payload
    summary and idempotency lines. The id is no credential (Retrieve
    Transaction needs the store's own OAuth token), and the handlers log it
    as it is: staff look it up in Viva's dashboard by it."""
    return (
        hashlib.sha256(str(transaction_id).encode()).hexdigest()[:16]
        if transaction_id
        else ""
    )


def ledger_charge(order: Order, transaction_id: str):
    """The ledger's CHARGE of *order* for Viva transaction
    *transaction_id*, or ``None``. Viva's ids are GUIDs, compared without
    regard to case."""
    from order.enum.payment_transaction import PaymentTransactionKind
    from order.models.payment_transaction import PaymentTransaction
    from order.payments.payment import VivaWalletPaymentProvider

    if not transaction_id:
        return None
    return PaymentTransaction.objects.filter(
        order=order,
        kind=PaymentTransactionKind.CHARGE,
        provider=VivaWalletPaymentProvider.code,
        provider_reference__iexact=transaction_id,
    ).first()


def verify_transaction(transaction_id):
    from order.payments.payment import VivaWalletPaymentProvider

    try:
        provider = VivaWalletPaymentProvider()
        logger.info(
            "verify_transaction: calling provider.get_payment_status(%s) "
            "live_mode=%s api_url=%s",
            transaction_id,
            provider.live_mode,
            provider.api_url,
        )
        status, data = provider.get_payment_status(transaction_id)
        logger.info(
            "verify_transaction: success | transaction_id=%s | "
            "status=%s | raw_status=%s",
            transaction_id,
            status,
            data.get("raw_status") if isinstance(data, dict) else None,
        )
        return status, data
    except Exception:
        logger.exception("verify_transaction: FAILED for %s", transaction_id)
        return None, {}


def retrieve_transaction(transaction_id):
    """Retrieve Transaction for a webhook event's id, or raise.

    Raises ``RuntimeError`` (→ 500, Viva retries) when verification is
    UNAVAILABLE: the infrastructure failed, or Viva could not cleanly
    retrieve the transaction (a forged or unknown id is a 404, and
    ``get_payment_status`` raises for it as for any error, never
    answering a status that could satisfy a failed check). Roll back and
    retry rather than trust the unverified event; the same raise is what
    lets the multi-tenant candidate loop move on from a tenant that does
    not own the transaction.
    """
    verified_status, verified_data = verify_transaction(transaction_id)
    if verified_status is None:
        raise RuntimeError(
            f"Viva transaction verification unavailable for {transaction_id}"
        )
    return verified_status, verified_data


def transaction_belongs_to_order(order, verified_data) -> bool:
    """Does the VERIFIED transaction actually belong to this order?

    Viva's own guidance is to retrieve the transaction and validate the
    orderCode, statusId AND amount from that response
    (developer.viva.com/webhooks-for-payments/setting-up-webhooks). We
    checked statusId and amount but only logged the orderCode, which
    leaves the substitution the amount guard cannot see: the webhook body
    is unauthenticated, so anyone may post their OWN real TransactionId
    against SOMEONE ELSE'S OrderCode. We would resolve the victim's
    order, verify the attacker's transaction — genuinely COMPLETED — and
    settle the victim's order as soon as the two amounts happen to
    match, which costs the attacker nothing to arrange.

    An absent orderCode in the response is NOT treated as a match: the
    whole point is to confirm the link, and a confirmation we did not
    receive is not one we can assume.
    """
    return transaction_belongs_to_codes(
        order_viva_codes(order), verified_data, f"order {order.id}"
    )


def transaction_belongs_to_codes(
    codes: set[str], verified_data, subject: str
) -> bool:
    """:func:`transaction_belongs_to_order` for any holder of Viva order
    codes — an order's issued set, a gift-card purchase's single code.
    """
    reported = (
        verified_data.get("order_code")
        if isinstance(verified_data, dict)
        else None
    )
    if not reported:
        # Viva documents orderCode as part of the Retrieve Transaction
        # response, so its absence is an abnormal answer rather than a
        # mismatch. Raise instead of refusing: a 500 has Viva redeliver,
        # where a skip would ack the event and strand a real payment.
        raise RuntimeError(
            f"Viva transaction {verified_data.get('payment_id')!r} was "
            "verified without an orderCode — cannot confirm it belongs to "
            f"{subject}"
        )
    return str(reported) in codes


def flag_amount_mismatch(
    order, transaction_id, verified_amount, expected_amount
) -> None:
    """Record a verified charge whose amount does not match the order, and
    page staff (``OrderService.flag_payment_mismatch``)."""
    from order.payments.payment import VivaWalletPaymentProvider
    from order.services import OrderService

    OrderService.flag_payment_mismatch(
        order,
        payment_id=str(transaction_id),
        provider=VivaWalletPaymentProvider.code,
        observed=verified_amount,
        expected=expected_amount,
    )


def handle_payment_created(
    order,
    event_data,
    transaction_id,
    *,
    verified: tuple[PaymentStatus | None, dict[str, Any]] | None = None,
):
    """Settle *order* for the Viva payment *transaction_id*.

    *verified* is Retrieve Transaction's answer when the caller asked it
    before locking the order (:func:`apply_event`); without it the
    transaction is retrieved here.
    """
    from django.utils import timezone

    from order.models.viva_webhook_event import (
        VivaWebhookEvent,
    )

    logger.info(
        "handle_payment_created START | order=%s | transaction_id=%s | "
        "current_payment_status=%s | current_status=%s",
        order.id,
        transaction_id,
        order.payment_status,
        order.status,
    )

    # Per Viva docs: check StatusId from the webhook payload first.
    # "F" = Finished (successful). Any other value means the payment
    # is not yet complete.
    status_id = event_data.get("StatusId", "")
    logger.info(
        "Viva webhook StatusId for order %s: '%s'",
        order.id,
        status_id,
    )
    if status_id and status_id != "F":
        logger.warning(
            "Viva webhook StatusId is '%s' (not 'F') for order %s — "
            "skipping payment update",
            status_id,
            order.id,
        )
        return

    # Per Viva docs: verify via Retrieve Transaction API as extra
    # confirmation. Do NOT trust the webhook payload alone.
    # A payment-created event without a TransactionId is not verifiable
    # and must be rejected to prevent fake payment completions.
    if not transaction_id:
        logger.error(
            "Viva webhook event 1796 missing TransactionId for order %s "
            "— cannot verify, skipping payment update",
            order.id,
        )
        return

    logger.info(
        "Calling Viva Retrieve Transaction API for transaction %s (order %s)",
        transaction_id,
        order.id,
    )
    verified_status, verified_data = (
        verified if verified is not None else verify_transaction(transaction_id)
    )
    # Allowlisted fields only — never log the raw provider dict (it may
    # carry cardholder data; see the redaction policy at the top of the
    # webhook handler).
    logger.info(
        "Viva Retrieve Transaction result for %s: status=%s "
        "raw_status=%s amount=%s order_code=%s",
        transaction_id,
        verified_status,
        verified_data.get("raw_status")
        if isinstance(verified_data, dict)
        else None,
        verified_data.get("amount")
        if isinstance(verified_data, dict)
        else None,
        verified_data.get("order_code")
        if isinstance(verified_data, dict)
        else None,
    )
    if verified_status is None:
        logger.error(
            "Could not verify Viva transaction %s "
            "— leaving event unprocessed so Viva can retry",
            transaction_id,
        )
        # Raise so the outer atomic block rolls back; the
        # VivaWebhookEvent row is never written, Viva retries fresh.
        #
        # Treating a Retrieve-Transaction ERROR (e.g. a 404 because the id
        # is not in THIS tenant's Viva account) as "unavailable" rather
        # than as a confirmed non-completion is
        # what lets a webhook whose transaction belongs to a DIFFERENT
        # tenant fall through to the next candidate in
        # ``_handle_webhook_event`` — and, independent of multi-tenancy,
        # stops a transient Viva error from being silently acknowledged
        # as "payment not completed". Mirrors
        # ``_verify_viva_terminal_transaction`` (1797/1798).
        raise RuntimeError(
            f"Viva transaction verification unavailable for {transaction_id}"
        )
    # Defence in depth: confirm the verified transaction amount matches
    # this order's total. Without this check an attacker who knows their
    # own valid TransactionId and another user's OrderCode could replay
    # a low-value transaction against a high-value order — the IP gate
    # is non-blocking and the Retrieve Transaction API only proves the
    # transaction exists at Viva, not that it was for this order.
    verified_amount_raw = (
        verified_data.get("amount") if isinstance(verified_data, dict) else None
    )
    # The transaction must be THIS order's before its amount or status
    # mean anything (Viva's documented three-way check).
    if not transaction_belongs_to_order(order, verified_data):
        logger.error(
            "Viva transaction %s reports order_code %r, which is not one of "
            "order %s's issued codes — refusing to mark as paid",
            transaction_id,
            verified_data.get("order_code")
            if isinstance(verified_data, dict)
            else None,
            order.id,
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    # Currency first: an amount only means something once we know it is
    # denominated in the order's currency. Viva reports ISO 4217 in its
    # NUMERIC form ("978"); the provider normalises that to the
    # alphabetic code, so this compares like with like.
    order_total = order.calculate_order_total_amount()
    verified_currency = (
        verified_data.get("currency")
        if isinstance(verified_data, dict)
        else None
    )
    if verified_currency != order_total.currency.code:
        logger.error(
            "Viva transaction %s is in %r but order %s is in %s — refusing "
            "to mark as paid",
            transaction_id,
            verified_currency,
            order.id,
            order_total.currency.code,
        )
        flag_amount_mismatch(
            order, transaction_id, verified_currency, order_total.currency.code
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    # The amount is required: a transaction that reports none (or none we
    # can read) cannot be told from one for a different sum, and status
    # alone settled the order. Refused like a mismatch: the charge is
    # flagged for staff rather than dropped.
    verified_amount = provider_amount(verified_amount_raw)
    if verified_amount is None:
        logger.error(
            "Viva transaction %s reports no readable amount (%r) for order "
            "%s — refusing to mark as paid",
            transaction_id,
            verified_amount_raw,
            order.id,
        )
        flag_amount_mismatch(
            order, transaction_id, verified_amount_raw, order_total.amount
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED
    expected_amount = order_total.amount
    # Allow a 1-cent tolerance for any provider-side rounding.
    if abs(verified_amount - expected_amount) > Decimal("0.01"):
        logger.error(
            "Viva transaction %s amount mismatch: verified=%s "
            "expected=%s for order %s — refusing to mark as paid",
            transaction_id,
            verified_amount,
            expected_amount,
            order.id,
        )
        # Marking it paid would under-charge, so we do not — but
        # the money HAS left the customer (Viva verified a real
        # transaction), and the usual cause is a shopper paying
        # on a stale checkout tab after the total moved. Record
        # it on the order: without this the order just sat
        # PENDING and auto_cancel_stuck_pending_orders closed it
        # a day later with refund_payment=False, leaving a
        # charged customer, a cancelled order and no alert.
        flag_amount_mismatch(
            order, transaction_id, verified_amount, expected_amount
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    if verified_status != PaymentStatus.COMPLETED:
        logger.warning(
            "Viva transaction %s not completed (status: %s) — skipping",
            transaction_id,
            verified_status,
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    # The same payment again (a replay, or the webhook of a payment read
    # back from Viva already): booked, settled and confirmed once.
    # Viva's ids are GUIDs and compared without regard to case.
    if (
        order.payment_status == PaymentStatus.COMPLETED
        and (order.payment_id or "").lower() == str(transaction_id).lower()
    ):
        logger.info(
            "Viva transaction %s already settled order %s",
            transaction_hash(transaction_id),
            order.id,
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    logger.info(
        "Viva transaction %s VERIFIED COMPLETED — updating order %s",
        transaction_id,
        order.id,
    )

    from djmoney.money import Money

    from order.lifecycle.service import OrderLifecycleService
    from order.payments.payment import VivaWalletPaymentProvider
    from order.payments.payment_ledger import PaymentLedger
    from order.services import OrderService

    # The money is in, whatever state the order is in: a canceled order or
    # a second payment under another of its checkout codes is still a
    # charge, and the one a refund has to name. A charge the ledger holds
    # already (booked by a refund of it that came first, under the
    # spelling its ``ParentId`` used) keeps that row.
    booked = ledger_charge(order, transaction_id)
    PaymentLedger.record_charge(
        order,
        amount=Money(verified_amount, order_total.currency),
        provider=VivaWalletPaymentProvider.code,
        reference=booked.provider_reference if booked else transaction_id,
    )

    # Before the settled guard: a canceled order's payment is already
    # settled (``Order.save`` settles an unpaid one to CANCELED), so
    # behind the guard a verified charge on it was dropped with only a
    # WARNING — the customer charged, the order dead, nobody told. The
    # money is booked, staff are paged, nothing ships (G0281).
    if OrderService.is_payment_after_cancel(order):
        if not order.metadata:
            order.metadata = {}
        order.metadata["viva_transaction_id"] = transaction_id
        OrderService.record_payment_after_cancel(
            order, payment_id=transaction_id, payment_method="viva_wallet"
        )
        from order.payments.payment_events import publish_payment_status

        publish_payment_status(order)
        return None

    # A second payment on an order another transaction already paid: the
    # ledger booked it above and, when it takes the order past what it is
    # for, ``PaymentLedger.sync_overcharge`` told staff. The order keeps
    # its first ``payment_id``; confirming it again would overwrite that
    # id and mail the customer a second confirmation.
    if (
        order.payment_status == PaymentStatus.COMPLETED
        and order.payment_id
        and order.payment_id.lower() != str(transaction_id).lower()
    ):
        logger.error(
            "Viva transaction %s is a second payment on order %s, already "
            "paid by %s: recorded in the ledger, order left as it is",
            transaction_id,
            order.id,
            order.payment_id,
        )
        return None

    # Guard: a stale or out-of-order Viva webhook must not un-refund or
    # un-cancel an order that is already in a settled financial state.
    # Viva does NOT guarantee delivery order.  COMPLETED is allowed
    # through (idempotent — the PENDING→PROCESSING block below is gated
    # on order.status so no double-shipment dispatch occurs).
    _refund_or_cancel = {
        PaymentStatus.REFUNDED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.CANCELED,
    }
    if order.payment_status in _refund_or_cancel:
        logger.warning(
            "Ignoring stale payment_created (Viva) for order %s: "
            "payment_status already %s",
            order.id,
            order.payment_status,
        )
        return

    # Capture previous state for audit log before mutating
    previous_payment_status = order.payment_status

    # Set all fields at once to avoid multiple DB writes
    order.metadata["viva_transaction_id"] = transaction_id
    order.payment_id = transaction_id
    order.payment_status = PaymentStatus.COMPLETED
    order.payment_method = "viva_wallet"
    if not order.amount_due:
        order.amount_due = order.calculate_order_total_amount()

    update_fields = [
        "metadata",
        "payment_id",
        "payment_status",
        "payment_method",
        "amount_due",
        "amount_due_currency",
    ]

    if order.status == OrderStatus.PENDING:
        # Mirror the Stripe handler's PR #7 suppression: the Viva
        # webhook dispatches ``send_order_confirmation_email`` directly
        # below — that already conveys "your order is being processed".
        # Without this pre-stamp the post-save signal would fire a
        # second PROCESSING email + toast within ms of the
        # confirmation email.
        OrderLifecycleService.suppress_customer_status_notifications(
            order, OrderStatus.PROCESSING.value
        )
        order.status = OrderStatus.PROCESSING
        order.status_updated_at = timezone.now()
        update_fields += ["status", "status_updated_at"]

    order.save(update_fields=update_fields)

    OrderHistory.log_payment_update(
        order=order,
        previous_value={"payment_status": previous_payment_status},
        new_value={
            "payment_status": PaymentStatus.COMPLETED,
            "payment_id": transaction_id,
            "provider": "viva_wallet",
        },
    )

    from order.payments.payment_events import publish_payment_status
    from order.tasks import send_order_confirmation_email

    publish_payment_status(order)

    dispatch_on_commit(send_order_confirmation_email, [order.id])

    # Enqueue the carrier's delivery-request creation. Provider-agnostic
    # dispatch through the registry — Stripe's ``handle_payment_succeeded``
    # uses the same hook. Without this call, ACS (and any future
    # carrier) orders paid via Viva would never get their shipment task
    # fired on payment success, leaving the order stuck in PROCESSING
    # with no voucher / no parcel.
    from shipping.services import ShippingService

    ShippingService.dispatch_create_shipment_task(order)


def read_payment_created(
    order: Order, event_data: dict[str, Any], transaction_id: str
) -> dict[str, Any]:
    """:data:`EventRead` of a 1796: Retrieve Transaction for a payment the
    event says finished, judged by :func:`handle_payment_created`. One
    that names no transaction or another status reads nothing: the
    handler refuses it as it is."""
    if transaction_id and event_data.get("StatusId", "") in {"", "F"}:
        return {"verified": verify_transaction(transaction_id)}
    return {}


def apply_event(
    order: Order,
    *,
    event_type_id: int | None,
    event_data: dict[str, Any],
    transaction_id: str,
    order_code: str,
    status_id: str,
    handle: EventHandler | None,
    read: EventRead | None = None,
) -> str | None:
    """Run *handle* for one Viva event of *order*, once per transaction.

    *read* takes what the event needs from Viva before the order row is
    locked (:data:`EventRead`); *handle* judges it under the lock.

    The ``VivaWebhookEvent`` row keyed on ``(transaction_id,
    event_type_id)`` is the idempotency record: written last, in the same
    transaction as what *handle* changed, so an event that was applied is
    never applied again, whoever brings it (Viva's webhook, its
    redelivery, or :func:`settle_landed_payment`). Answers the outcome
    recorded, or ``None`` when the event had been applied already.

    Raises ``RuntimeError`` when the transaction could not be verified
    with Viva: nothing is written and the event can be applied later.
    """
    from order.models.viva_webhook_event import VivaWebhookEvent

    txn_hash = transaction_hash(transaction_id)

    def applied() -> bool:
        return bool(
            transaction_id
            and event_type_id is not None
            and VivaWebhookEvent.objects.filter(
                transaction_id__iexact=str(transaction_id),
                event_type_id=event_type_id,
            ).exists()
        )

    if applied():
        logger.info(
            "Viva event already processed | event_type=%s | txn_hash=%s "
            "(idempotency hit)",
            event_type_id,
            txn_hash,
        )
        return None

    taken = (
        read(order, event_data, transaction_id)
        if read is not None and handle is not None
        else {}
    )

    with transaction.atomic():
        # Re-fetch with row lock to prevent race conditions
        order = Order.objects.select_for_update().get(pk=order.pk)

        # Double-check idempotency after acquiring lock
        if applied():
            logger.info(
                "Viva event_type=%s txn_hash=%s processed by a parallel "
                "request — skipping",
                event_type_id,
                txn_hash,
            )
            return None

        logger.info(
            "Viva dispatching event_type=%s for order #%s",
            event_type_id,
            order.id,
        )
        # Every handler reports a skip so the audit row does not claim
        # work that never happened (and, as it permanently dedups the
        # transaction, so an operator can tell a refund that was applied
        # from one that was refused).
        if handle is None:
            logger.info("Unhandled Viva Wallet event type: %s", event_type_id)
            outcome = VivaWebhookEvent.OUTCOME_SKIPPED
        else:
            outcome = (
                handle(order, event_data, transaction_id, **taken)
                or VivaWebhookEvent.OUTCOME_PROCESSED
            )

        # Persist the idempotency row last — if the handler raised the
        # row was never written, and the next attempt gets a fresh shot.
        # Only events that have both keys are recorded; an empty
        # ``transaction_id`` would collapse every payload-less event onto
        # one row.
        if transaction_id and event_type_id is not None:
            VivaWebhookEvent.objects.create(
                transaction_id=str(transaction_id),
                event_type_id=event_type_id,
                order=order,
                order_code=str(order_code or ""),
                status_id=str(status_id or ""),
                outcome=outcome,
            )
    return outcome


def settle_landed_payment(order: Order) -> LandedPayment:
    """Settle the payments Viva took for *order*, whether or not their
    webhook landed, and say what Viva holds (``LandedPayment``).

    Each charge Viva lists on one of the order's payment orders
    (``VivaWalletPaymentProvider.landed_charges``) is applied as the
    Transaction Payment Created event its webhook carries
    (:func:`apply_event` with :func:`handle_payment_created`): the
    transaction is verified with Viva, and the ledger row, the status and
    the confirmation are the webhook's own, once. A charge Viva could not
    verify, or that settles nothing (another amount, a canceled order),
    is left to those checks, which record and flag it as for the webhook.
    A payment refunded or disputed since (``R``, ``M``...) is applied by
    no webhook. Viva holding money that settled nothing, for either
    reason, flags the order for the store's review
    (:func:`_flag_for_review`): the read-back settles it, flags it, or
    finds nothing taken, and never answers ``UNSETTLED`` for a run to
    repeat.

    Raises ``PaymentStatusUnavailableError`` when Viva could not be read:
    a payment may have landed, so the caller must not act as if none did.
    """
    from order.payments.payment import (
        VIVA_STATUS_FINISHED,
        VivaWalletPaymentProvider,
    )

    landed = VivaWalletPaymentProvider().landed_charges(order)
    for charge in landed:
        if charge.status_id != VIVA_STATUS_FINISHED:
            continue
        logger.info(
            "Viva holds payment txn_hash=%s for order %s: settling it as "
            "its webhook would",
            transaction_hash(charge.transaction_id),
            order.id,
        )
        try:
            apply_event(
                order,
                event_type_id=VIVA_TRANSACTION_PAYMENT_CREATED,
                event_data={"StatusId": "F"},
                transaction_id=charge.transaction_id,
                order_code=charge.order_code,
                status_id="F",
                handle=handle_payment_created,
                read=read_payment_created,
            )
        except RuntimeError as e:
            raise PaymentStatusUnavailableError(charge.transaction_id) from e
    if not landed:
        return LandedPayment.NONE
    order.refresh_from_db()
    if order.payment_status == PaymentStatus.COMPLETED or (
        payment_hold.awaits_payment_review(order)
    ):
        return LandedPayment.SETTLED
    # Viva took money that settled nothing: refunded or disputed since, or
    # refused by the checks without a flag. The store decides it.
    unsettled = next(
        (c for c in landed if c.status_id != VIVA_STATUS_FINISHED), landed[0]
    )
    _flag_for_review(order, unsettled)
    return LandedPayment.SETTLED


def _flag_for_review(order: Order, charge: LandedCharge) -> None:
    """Put *order* on the store's review worklist for *charge*, a payment
    Viva took that settled nothing (refunded or disputed before any
    webhook reached the order, or refused by the payment checks): what is
    held is the store's to reconcile (``OrderService.flag_payment_mismatch``,
    which flags a payment once). Under the order's lock: an order paid or
    moved on meanwhile is left as it is."""
    from djmoney.money import Money

    from order.payments.payment import VivaWalletPaymentProvider
    from order.services import OrderService

    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if (
            locked.status != OrderStatus.PENDING
            or locked.payment_status == PaymentStatus.COMPLETED
        ):
            return
        expected = locked.calculate_order_total_amount()
        OrderService.flag_payment_mismatch(
            locked,
            payment_id=charge.transaction_id,
            provider=VivaWalletPaymentProvider.code,
            observed=(
                f"{Money(charge.amount, expected.currency)} "
                f"(Viva StatusId {charge.status_id})"
            ),
            expected=expected,
        )
