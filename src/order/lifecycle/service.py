"""An order's life after it is created: the status machine, the lookups,
reordering, and cancellation with its courier and refund follow-ups.

``OrderLifecycleService.update_order_status`` is the one way an order
changes status; ``cancel_order`` cancels one, refunding through
``order.refunds.service.RefundService``.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from order.accounting.invoicing import hold_receipt_for_cancel
from order.checkout.attribution import (
    AttributionInput,
)
from order.checkout.attribution_service import OrderAttributionService
from order.enum.attribution import OrderSourceType
from order.enum.status import (
    OrderStatus,
    PaymentStatus,
)
from order.exceptions import (
    CourierUnreachableError,
    DisputeOpenError,
    InvalidStatusTransitionError,
    OrderCancellationError,
    OrderNotCancelableError,
    ParcelWithCourierError,
    PaymentError,
    RefundOutstandingError,
    StockReservationError,
)
from order.inventory.stock import StockManager
from order.models.order import (
    Order,
    status_update_reservation_key,
)
from order.models.stock_log import StockLog
from order.models.stock_reservation import StockReservation
from order.payments.payment_ledger import (
    PaymentLedger,
)
from order.refunds.service import RefundService
from order.signals import (
    order_shipment_cancel_answered,
)
from shipping.exceptions import (
    CarrierBusyError,
    CarrierError,
    CarrierUnavailableError,
)

logger = logging.getLogger(__name__)


# ``metadata['cancellation']['shipment_cancel']`` of an order canceled
# because its shipment already was: the courier was not asked, and the
# shipment is canceled. The same shape ``cancel_attached_shipment`` writes.
SHIPMENT_ALREADY_CANCELED: dict[str, Any] = {
    "attempted": False,
    "dispatched": True,
}

# ``metadata['shipment_cancel_pending']`` of an order a staff or system
# cancel committed and whose courier has not answered yet. It is written
# with the cancel, in its transaction, and removed together with the
# courier's answer (``cancel_attached_shipment``), which is asked for only
# after that commit. A worker that stops in between leaves the flag, and
# ``order.tasks.finish_pending_shipment_cancels`` asks the courier then.
SHIPMENT_CANCEL_PENDING_FLAG = "shipment_cancel_pending"

# ``metadata['shipment_cancel_unanswered']`` of a pending cancel whose
# courier gave no answer (``CarrierUnavailableError``): ``since`` (the
# first such attempt), ``attempts`` and ``last_error``. It stays beside
# :data:`SHIPMENT_CANCEL_PENDING_FLAG` while the sweep asks again, for
# ``ORDER_SHIPMENT_CANCEL_RETRY_HOURS`` after the cancel, and goes with it
# once an answer is recorded.
SHIPMENT_CANCEL_UNANSWERED_KEY = "shipment_cancel_unanswered"


def shipment_cancel_retry_until(canceled_at: datetime | None) -> datetime:
    """When the courier of a pending shipment cancel made at *canceled_at*
    (the order's ``status_updated_at``) stops being asked again after
    giving no answer."""
    return (canceled_at or timezone.now()) + timedelta(
        hours=settings.ORDER_SHIPMENT_CANCEL_RETRY_HOURS
    )


def shipment_cancel_wait_minutes() -> int:
    """The longest a canceled order left with
    :data:`SHIPMENT_CANCEL_PENDING_FLAG` waits before
    ``finish_pending_shipment_cancels`` asks its courier: the age the
    sweep takes a cancel at, plus the time between two of its runs."""
    return (
        math.ceil(settings.ORDER_SHIPMENT_CANCEL_SWEEP_AFTER_SECONDS / 60)
        + settings.ORDER_SHIPMENT_CANCEL_SWEEP_EVERY_MINUTES
    )


#: The statuses an order may move to from each status; any other move is
#: refused with ``InvalidStatusTransitionError``.
ALLOWED_TRANSITIONS = {
    OrderStatus.PENDING: [OrderStatus.PROCESSING],
    OrderStatus.PROCESSING: [OrderStatus.SHIPPED],
    OrderStatus.SHIPPED: [
        OrderStatus.DELIVERED,
        OrderStatus.RETURNED,
    ],
    OrderStatus.DELIVERED: [
        OrderStatus.COMPLETED,
        OrderStatus.RETURNED,
    ],
    OrderStatus.CANCELED: [],
    OrderStatus.COMPLETED: [],
    OrderStatus.RETURNED: [OrderStatus.REFUNDED],
    OrderStatus.REFUNDED: [],
}


class OrderLifecycleService:
    @classmethod
    def get_order_by_id(cls, order_id: int) -> Order:
        """Get order by ID with optimized queryset."""
        return Order.objects.for_detail().get(id=order_id)

    @classmethod
    def get_order_by_uuid(cls, uuid: str) -> Order:
        """Get order by UUID with optimized queryset."""
        return Order.objects.for_detail().get(uuid=uuid)

    @classmethod
    def get_agent_order_by_uuid(cls, uuid: str) -> Order:
        """Get an order an AI agent placed, by UUID."""
        return Order.objects.for_detail().get(
            uuid=uuid, attribution__source_type=OrderSourceType.AGENT
        )

    @classmethod
    @transaction.atomic
    def update_order_status(
        cls,
        order: Order,
        new_status: str,
        *,
        silent_for_customer: bool = False,
    ) -> Order:
        """Validate + apply a status transition.

        ``silent_for_customer=True`` pre-stamps the suppression flags
        (see ``suppress_customer_status_notifications``) so the
        customer email/WS toast for THIS transition is skipped while
        history, signals and internal state still flow. Used by chained
        transitions — e.g. the carrier bridge that walks PROCESSING →
        SHIPPED → RETURNED must not tell the customer "your order is on
        the way" moments before the return notification.

        CANCELED is not a transition of this table: a cancel releases
        stock, settles or refunds the payment and records why, and
        :meth:`cancel_order` is the one path that does. Asking for it
        here raises ``InvalidStatusTransitionError``.
        """
        if not new_status:
            raise ValueError("New status cannot be empty")

        # Lock + re-read the CURRENT status so concurrent transitions
        # serialize and validate against the committed row, not the
        # caller's stale snapshot (G0285). Sync the caller's object to
        # the committed status so callers that read ``order`` after this
        # (rather than the returned instance) stay consistent.
        current_status = (
            Order.objects.select_for_update()
            .values_list("status", flat=True)
            .get(pk=order.pk)
        )
        order.status = current_status

        if order.status == new_status:
            logger.info("Order %s status is already %s", order.id, new_status)
            return order

        if new_status not in ALLOWED_TRANSITIONS.get(order.status, []):
            logger.warning(
                "Invalid status transition for order %s: from %s to %s",
                order.id,
                order.status,
                new_status,
            )
            raise InvalidStatusTransitionError(
                current_status=order.status,
                new_status=new_status,
                allowed=[
                    str(s) for s in ALLOWED_TRANSITIONS.get(order.status, [])
                ],
            )

        # REFUNDED says the customer was paid back, and the loyalty
        # reversal and the status email follow the status: not while the
        # ledger still holds money that can be refunded.
        if new_status == OrderStatus.REFUNDED and PaymentLedger.refund_open(
            order
        ):
            raise RefundOutstandingError(PaymentLedger.outstanding(order))

        old_status = order.status

        if silent_for_customer:
            cls.suppress_customer_status_notifications(order, new_status)

        order.status = new_status
        order.status_updated_at = timezone.now()
        order.save(update_fields=["status", "status_updated_at"])

        # Note: order_status_changed signal is sent automatically by
        # handle_order_post_save when the status changes.

        logger.info(
            "Order %s status updated from %s to %s",
            order.id,
            old_status,
            new_status,
        )

        return order

    @classmethod
    def confirm(cls, order: Order) -> bool:
        """Confirm *order*, once, and send ``order_confirmed``.

        The one way an order is announced: its receiver
        (``order.signals.handlers.handle_order_confirmed``) sends the
        customer's confirmation email, the staff "New order" email and one
        in-app note, and the recommendation engine records what the order
        bought from its suggestions. Called at creation for an order with
        nothing left to pay online (``confirmation.confirmed_at_creation``)
        and by every path that settles an online payment: the webhooks,
        the buyer's return, a poll, the sweep's read-back.

        ``confirmed_at`` is claimed with one ``UPDATE ... WHERE
        confirmed_at IS NULL``: whichever path reports the payment first
        confirms it, and every other answers ``False`` and announces
        nothing. In the caller's transaction, so a rollback takes the claim
        and the queued messages with it.
        """
        from order.signals import order_confirmed

        now = timezone.now()
        claimed = Order.objects.filter(
            pk=order.pk, confirmed_at__isnull=True
        ).update(confirmed_at=now)
        if not claimed:
            return False
        order.confirmed_at = now
        order_confirmed.send(sender=Order, order=order)
        logger.info("Order %s confirmed", order.id)
        return True

    @classmethod
    def suppress_customer_status_notifications(
        cls, order: Order, new_status: str
    ) -> None:
        """Pre-stamp metadata so the next ``new_status`` transition
        skips the customer email + WS toast.

        Used by chained transitions (DELIVERED → COMPLETED auto-advance,
        admin tracking promotion that hops PENDING → PROCESSING → SHIPPED,
        online payment succeeded firing PENDING → PROCESSING right
        before the order_received confirmation email lands) where the
        chained-into status would arrive at the customer's inbox /
        notification bell within ~ms of the previous one and feel like
        spam.

        Internal state still flows: ``order_status_changed`` signal
        fires, OrderHistory logs the transition, the post-save handler
        runs. Only the user-visible ``send_order_status_update_email``
        + ``notify_order_status_changed_live`` dispatches are skipped.

        ``status_update_reservation_key`` is the same key the email
        task uses to dedupe — pre-stamping it makes the task short-
        circuit. The matching ``suppress_status_ws_<status>`` flag
        is read by ``handle_order_status_changed`` for the live-
        notification dispatch.
        """
        email_flag = status_update_reservation_key(order.id, new_status)
        ws_flag = f"suppress_status_ws_{new_status}"
        if not order.metadata:
            order.metadata = {}
        order.metadata[email_flag] = True
        order.metadata[ws_flag] = True
        order.save(update_fields=["metadata"])

    @classmethod
    def maybe_advance_to_completed(
        cls, order: Order, *, silent_for_customer: bool = False
    ) -> Order:
        """Auto-advance ``order`` from DELIVERED to COMPLETED when paid.

        The canonical state-machine table allows DELIVERED → COMPLETED,
        but nothing in the wild was actually invoking it: online orders
        ended at DELIVERED, COD orders ended at DELIVERED + payment_
        status=PENDING (until the reconcile pass flips them). Without
        this helper, "completed" was an admin-only manual flip.

        Triggers from two call-sites:
        * Carrier event handlers (ACS poll, BoxNow webhook) right after
          they advance to DELIVERED for an already-paid online order.
          Pass ``silent_for_customer=True`` here — the customer just
          got the DELIVERED email + toast and a COMPLETED message ~ms
          later would feel like a duplicate.
        * COD reconcile, after flipping payment_status to COMPLETED.
          Also passes ``silent_for_customer=True`` — the customer paid
          the courier in person and already received the DELIVERED
          notification; the reconcile is internal bookkeeping
          (site-owner decision 2026-07-11: it never emails customers).

        Idempotent and silent when the order is not eligible — a
        non-paid order or one already past DELIVERED no-ops.

        Status fields are read with ``values_list`` rather than
        ``refresh_from_db(fields=...)``: the latter leaves other
        columns deferred, and ``Order.__init__`` lazy-loads them when
        it snapshots ``_original_tracking_number`` etc., which
        recurses through the manager.
        """
        row = (
            Order.objects.filter(pk=order.pk)
            .values("status", "payment_status")
            .first()
        )
        if not row:
            return order
        if row["status"] != OrderStatus.DELIVERED:
            return order
        if row["payment_status"] != PaymentStatus.COMPLETED:
            return order
        order.status = row["status"]
        order.payment_status = row["payment_status"]
        if silent_for_customer:
            cls.suppress_customer_status_notifications(
                order, OrderStatus.COMPLETED.value
            )
        try:
            return cls.update_order_status(order, OrderStatus.COMPLETED)
        except InvalidStatusTransitionError as exc:
            logger.warning(
                "Order %s DELIVERED -> COMPLETED auto-advance rejected by "
                "state machine: %s",
                order.id,
                exc,
            )
            return order

    @classmethod
    def get_user_orders(cls, user_id: int) -> QuerySet:
        """Get all orders for a user with optimized queryset."""
        return (
            Order.objects.for_list()
            .filter(user_id=user_id)
            .order_by("-created_at")
        )

    @classmethod
    @transaction.atomic
    def reorder_to_cart(
        cls, order: Order, user, *, attribution: AttributionInput
    ) -> dict[str, Any]:
        """Add each item from a past order back into the user's active cart.

        Items with insufficient stock or inactive products are recorded in
        `skipped_items` rather than rejecting the whole reorder. The cart
        line (existing quantity plus the reordered one) is capped at
        current stock; a shortfall is reported with reason ``partial``.

        A reorder that fills an empty cart is the cart's first touch,
        recorded from ``attribution`` (the request's own signals: a
        reorder carries no storefront capture).
        """
        from cart.models import Cart, CartItem
        from cart.services import CartAttributionService

        cart, _created = Cart.objects.get_or_create(user=user)
        # Serialise reorders into one cart: each would otherwise read "no
        # line yet" for a product and the second insert would break the
        # one-line-per-product constraint. Lines stay locked below too,
        # because add-to-cart writes them without this lock.
        cart = Cart.objects.select_for_update().get(pk=cart.pk)

        added: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        touch = OrderAttributionService.classify_for_store(attribution)
        captured = False

        for item in order.items.select_related("product").all():
            product = item.product
            requested = item.quantity

            if not getattr(product, "active", True):
                skipped.append(
                    {
                        "product_id": product.id,
                        "requested_quantity": requested,
                        "added_quantity": 0,
                        "reason": "inactive",
                    }
                )
                continue

            available = getattr(product, "stock", 0) or 0
            if available <= 0:
                skipped.append(
                    {
                        "product_id": product.id,
                        "requested_quantity": requested,
                        "added_quantity": 0,
                        "reason": "out_of_stock",
                    }
                )
                continue

            existing = (
                CartItem.objects.select_for_update()
                .filter(cart=cart, product=product)
                .first()
            )
            # Cap the MERGED line at stock, as add-to-cart and the
            # guest→user cart merge do; a line already holding it all
            # takes nothing more and is never reduced.
            in_cart = existing.quantity if existing else 0
            to_add = max(0, min(requested, available - in_cart))

            entry = {
                "product_id": product.id,
                "requested_quantity": requested,
                "added_quantity": to_add,
                "reason": "partial" if to_add < requested else "",
            }
            if to_add < requested:
                skipped.append(entry)
            if to_add == 0:
                continue

            if not captured:
                CartAttributionService.capture(cart, touch)
                captured = True

            if existing:
                existing.quantity += to_add
                existing.save(update_fields=["quantity"])
            else:
                CartItem.objects.create(
                    cart=cart, product=product, quantity=to_add
                )
            added.append(entry)

        return {
            "cart_id": cart.id,
            "added_items": added,
            "skipped_items": skipped,
        }

    @classmethod
    def cancel_order(
        cls,
        order: Order,
        reason: str = "",
        refund_payment: bool = True,
        canceled_by: int | None = None,
        *,
        customer_initiated: bool = True,
        shipment_already_canceled: bool = False,
    ) -> tuple[Order, dict[str, Any] | None]:
        """Cancel an order: stock back, status CANCELED, voucher, refund.

        ``shipment_already_canceled`` is for a cancel that follows the
        shipment's own (:meth:`cancel_for_dead_shipment`): the courier is
        not asked at all, before or after, and the order records
        :data:`SHIPMENT_ALREADY_CANCELED`.

        ``customer_initiated`` is the caller's call, taken from what it
        already knows (the cancel endpoint: the requester is not store
        staff). A customer cancel asks the courier to cancel the voucher
        FIRST and only goes on when the courier confirms or the order has
        no voucher. A courier that refuses (voucher already on a pickup
        list, parcel already at a locker) raises
        :class:`ParcelWithCourierError`, one that gives no answer raises
        :class:`CourierUnreachableError`, and either way nothing changes —
        no status, no refund, no shipment. That ask is a carrier HTTP
        call, so a customer cancel must not run inside an open
        transaction.

        Staff and system callers pass ``customer_initiated=False``: they
        cancel and refund regardless, the courier is asked afterwards on
        a best-effort basis, and a refusal is recorded in
        ``metadata['cancellation']['shipment_cancel']`` and in a staff
        history note. The default is the strict one, so a caller that
        forgets to choose is held to the courier's answer.

        That later ask runs once the cancel has COMMITTED
        (:meth:`cancel_attached_shipment`), so no order, product or
        shipment row is locked while the courier answers. A caller that
        holds no transaction reads the answer on the order it gets back.
        One that cancels inside its own transaction (the auto-cancel
        sweep, under its row lock) gets the order back before the
        courier is asked: the ask follows that transaction's commit.
        """
        customer_asked_courier = (
            customer_initiated and not shipment_already_canceled
        )
        if shipment_already_canceled:
            shipment_cancel = dict(SHIPMENT_ALREADY_CANCELED)
        elif customer_asked_courier:
            shipment_cancel = cls._ask_courier_to_cancel(order, reason)
        else:
            shipment_cancel = None
        try:
            canceled = cls._cancel_order_locked(
                order,
                reason,
                canceled_by,
                shipment_cancel=shipment_cancel,
            )
        except Exception:
            # The note is for a cancel that asked the courier, got the
            # voucher canceled and then failed: staff must issue a new
            # one. A shipment that was dead before this ran has no such
            # story to tell.
            if (
                customer_asked_courier
                and shipment_cancel is not None
                and shipment_cancel["dispatched"]
            ):
                cls._note_shipment_canceled_without_order(order)
            raise
        if shipment_cancel is None:
            # ``on_commit`` runs this at once when no transaction is open
            # (every caller but the auto-cancel sweep), and after the
            # caller's own commit otherwise: either way the courier is
            # never asked while a row lock is held.
            transaction.on_commit(
                lambda: cls.cancel_attached_shipment(canceled, reason)
            )
        # The cancel is committed; the refund is its own claim, call and
        # record (``refund_order``), so no transaction is open while the
        # provider answers.
        return canceled, cls._refund_canceled_order(
            canceled, reason, refund_payment
        )

    @classmethod
    def _refund_canceled_order(
        cls, order: Order, reason: str, refund_payment: bool
    ) -> dict[str, Any] | None:
        """Refund what a canceled order still holds: everything for a paid
        order, the rest for one a provider's dashboard already refunded in
        part. ``None`` when nothing is to be refunded."""
        if not (
            refund_payment
            and order.payment_id
            and order.pay_way
            and (order.is_paid or PaymentLedger.refundable(order) > 0)
        ):
            return None
        try:
            result = RefundService.refund_order(
                order=order,
                amount=None,
                reason=f"Order canceled: {reason}"
                if reason
                else "Order canceled",
            )
        except PaymentError as exc:
            # Refused before any provider was asked (a dispute is open,
            # nothing is left to refund, no credentials): the reason is
            # staff-readable, so it goes where staff look.
            from order.models.history import OrderHistory

            logger.warning(
                "Refund for canceled order %s not made: %s", order.id, exc
            )
            OrderHistory.log_note(
                order=order,
                note=(
                    "The canceled order was not refunded: "
                    f"{exc}"
                    + (
                        " The refund waits until the dispute closes; "
                        "refund the order then."
                        if isinstance(exc, DisputeOpenError)
                        else ""
                    )
                ),
            )
            return {
                "refunded": False,
                "error": str(_("The refund could not be processed.")),
                "message": "Order canceled but refund failed",
                "dispute_open": isinstance(exc, DisputeOpenError),
            }
        except Exception:
            logger.exception(
                "Error processing refund for canceled order %s", order.id
            )
            return {
                "refunded": False,
                "error": str(_("The refund could not be processed.")),
                "message": "Order canceled but refund failed",
            }
        if result.complete:
            return {
                "refunded": True,
                "refund_id": (
                    result.refund_ids[-1] if result.refund_ids else None
                ),
                "message": "Payment refunded successfully",
            }
        # ``declined["error"]`` carries raw provider exception text (see
        # src/order/payments/payment.py) — log it, never return it (CodeQL
        # py/stack-trace-exposure).
        logger.error(
            "Refund failed for canceled order %s: %s",
            order.id,
            (result.declined or {}).get("error"),
        )
        return {
            "refunded": False,
            "error": str(_("The refund could not be processed.")),
            # A refund split across payments can be declined part-way: say
            # so, the shares made are in the order's history.
            "message": (
                "Order canceled but only part of the payment was refunded"
                if result.paid_something
                else "Order canceled but refund failed"
            ),
        }

    @classmethod
    def _note_shipment_canceled_without_order(cls, order: Order) -> None:
        """Tell staff the order's shipment was canceled but the order was not.

        The shipment is canceled (at the courier, or only locally when no
        voucher had been issued) before the order row is locked, so the
        order cancel can still fail afterwards: most likely the order
        moved on while the courier answered (staff marking it shipped; a
        courier that accepts the cancel has not collected the parcel),
        and any other failure of the locked cancel ends the same way.
        The order then stays live with no voucher behind it. Nothing
        reverses a shipment cancel, so staff must create a new voucher;
        the ERROR line raises the logged-errors alert and the note is
        where staff read about the order. The note states only what is
        known (the shipment is canceled, the order is not, and its
        current status), never why. An order another request canceled
        in the meantime needs neither.
        """
        from order.models.history import OrderHistory

        current_status = Order.objects.values_list("status", flat=True).get(
            pk=order.pk
        )
        if current_status == OrderStatus.CANCELED:
            return
        logger.error(
            "Order %s: a customer cancel canceled its shipment, but the "
            "order (status %s) was not canceled; it needs a new voucher",
            order.id,
            current_status,
        )
        OrderHistory.log_note(
            order=order,
            note=(
                "The customer asked to cancel this order and its "
                "shipment was canceled, but the order itself was not "
                f"canceled (its status is {current_status}). The shipment "
                "no longer has a voucher: create a new one if the order "
                "is still to ship."
            ),
        )

    @classmethod
    def _ask_courier_to_cancel(
        cls, order: Order, reason: str
    ) -> dict[str, Any] | None:
        """Cancel the order's voucher at the courier before the order moves.

        Returns the ``shipment_cancel`` outcome to record on the order
        (the same shape :meth:`cancel_attached_shipment` writes), or
        ``None`` when the order's status no longer allows a cancel — the
        courier is not asked about an order that will not cancel, and
        the locked re-check in :meth:`_cancel_order_locked` raises the
        usual status error. Raises :class:`ParcelWithCourierError` when
        the courier refuses and :class:`CourierUnreachableError` when it
        gives no answer (``CarrierUnavailableError``). Only courier
        errors are the courier's answer: anything else is a fault of
        ours and propagates as itself.

        Runs before the order row is locked, outside any transaction of
        its own: the carrier calls are three-phase so they never hold a
        transaction open across the courier's HTTP round trip
        (``idle_in_transaction_session_timeout`` would kill it), and the
        order row stays unlocked while the courier answers. The locked
        re-check afterwards is what decides the order's fate
        (:meth:`_note_shipment_canceled_without_order` covers a
        shipment canceled for an order that then did not cancel).
        """
        from shipping.services import ShippingService

        current = Order.objects.get(pk=order.pk)
        if not current.can_be_canceled:
            return None

        try:
            dispatched = ShippingService.cancel_shipment(current, reason=reason)
        except CarrierUnavailableError as exc:
            logger.warning(
                "Customer cancel of order %s stopped: the courier gave no "
                "answer to the voucher cancel: %s",
                order.id,
                exc,
                exc_info=True,
            )
            raise CourierUnreachableError(
                order_id=order.id,
                reason=str(
                    _(
                        "We could not reach the courier to cancel your "
                        "parcel. Please try again in a few minutes."
                    )
                ),
            ) from exc
        except CarrierError as exc:
            logger.warning(
                "Customer cancel of order %s refused: the courier refused "
                "the voucher cancel: %s",
                order.id,
                exc,
                exc_info=True,
            )
            raise ParcelWithCourierError(
                order_id=order.id,
                reason=str(
                    _(
                        "Your parcel is already with the courier, so the "
                        "order can no longer be canceled. Contact the store "
                        "to arrange a return."
                    )
                ),
            ) from exc
        return {"attempted": True, "dispatched": dispatched}

    @classmethod
    @transaction.atomic
    def _cancel_order_locked(
        cls,
        order: Order,
        reason: str,
        canceled_by: int | None,
        *,
        shipment_cancel: dict[str, Any] | None,
    ) -> Order:
        # Lock the order row to prevent concurrent cancellation requests
        # from both restoring stock
        order = Order.objects.select_for_update().get(id=order.id)

        if not order.can_be_canceled:
            error_message = _(
                "Order in status {status} cannot be canceled. "
                "Only orders in PENDING or PROCESSING status can be canceled."
            ).format(status=order.status)
            logger.warning(
                "Cannot cancel order %s: %s", order.id, error_message
            )
            raise OrderNotCancelableError(
                order_id=order.id, reason=error_message
            )

        try:
            reservation_ids = (
                order.metadata.get("stock_reservation_ids", [])
                if order.metadata
                else []
            )
            # What the order is actually HOLDING — see the restore loop
            # below for why that is not the quantities it lists.
            outstanding = list(
                StockLog.objects.filter(
                    order_id=order.id,
                    operation_type__in=(
                        StockLog.OPERATION_DECREMENT,
                        StockLog.OPERATION_INCREMENT,
                    ),
                )
                .values("product_id")
                .annotate(net=Sum("quantity_delta"))
                .order_by("product_id")
            )
            # Every product this cancel touches (each reservation release
            # and each restore takes a product lock, and they run in the
            # order the order's own rows happen to come back) is locked
            # up front in pk order, so a cancel cannot deadlock with a
            # checkout or another cancel that shares a product.
            StockManager.lock_products(
                {row["product_id"] for row in outstanding}
                | set(
                    StockReservation.objects.filter(
                        id__in=reservation_ids
                    ).values_list("product_id", flat=True)
                )
            )

            # Release stock reservations if they exist
            for reservation_id in reservation_ids:
                try:
                    StockManager.release_reservation(reservation_id)
                    logger.info(
                        "Released reservation %s for canceled order %s",
                        reservation_id,
                        order.id,
                    )
                except StockReservationError as e:
                    # The periodic cleanup task runs every 5 min and
                    # flips expired reservations to consumed=True. On
                    # stale cancels (e.g. auto_cancel_stuck_pending_orders
                    # on 24h-old PENDING orders) this is the normal
                    # happy path, not an error — log at DEBUG.
                    if "already consumed" in str(e):
                        logger.debug(
                            "Reservation %s already consumed for order %s (expected for stale cancels)",
                            reservation_id,
                            order.id,
                        )
                    else:
                        logger.warning(
                            "Failed to release reservation %s: %s",
                            reservation_id,
                            e,
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to release reservation %s: %s",
                        reservation_id,
                        e,
                    )
                    # Continue with other reservations even if one fails

            # Restore the stock this order is actually HOLDING, which is
            # not the same as the quantities it lists. Only two operations
            # move physical stock — decrement_stock and
            # convert_reservation_to_sale, both logged as DECREMENT against
            # the order — while an OrderItem created any other way (the
            # standalone OrderItem admin, a repair script) never decremented
            # anything: handle_order_item_post_save deliberately skips it to
            # avoid double-counting the checkout path. Restoring per listed
            # item therefore invented stock that never left the shelf.
            #
            # Summing the order's own physical movements gets every case
            # right: nothing taken restores nothing, a partially restored
            # order restores only the remainder, and cancelling twice is a
            # no-op because the first restore's INCREMENT rows net it out.
            # RESERVE/RELEASE rows are excluded — a reservation is a logical
            # hold and leaves physical stock untouched (stock_before ==
            # stock_after).
            for row in outstanding:
                # ``net`` is a Sum over a non-null column, and every row
                # here IS a group, so it is always an int.
                quantity = -row["net"]
                if quantity <= 0:
                    continue
                product_id = row["product_id"]
                try:
                    StockManager.increment_stock(
                        product_id=product_id,
                        quantity=quantity,
                        order_id=order.id,
                        # ``reason`` defaults to "", and the bare f-string
                        # left "Order 270 canceled:" in the stock log —
                        # a dangling colon that reads as a truncated
                        # message when you are chasing a stock movement.
                        # Same shape the refund call below already uses.
                        reason=(
                            f"Order {order.id} canceled: {reason}"
                            if reason
                            else f"Order {order.id} canceled"
                        ),
                    )
                    logger.info(
                        "Restored stock for product %s: +%s (order %s canceled)",
                        product_id,
                        quantity,
                        order.id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to restore stock for product %s",
                        product_id,
                    )
                    # Continue with other products even if one fails

            old_status = order.status
            order.status = OrderStatus.CANCELED
            order.status_updated_at = timezone.now()
            # ``Order.save()`` settles an unpaid ``payment_status`` on this
            # transition (CANCELED, and the refund below moves a paid one
            # to REFUNDED); it adds the column to ``update_fields`` itself.

            if not order.metadata:
                order.metadata = {}

            order.metadata["cancellation"] = {
                "reason": reason,
                "canceled_at": timezone.now().isoformat(),
                "canceled_by": canceled_by,
                "previous_status": old_status,
            }
            if shipment_cancel is not None:
                # The courier already confirmed (a customer cancel asks it
                # first), or the shipment was canceled before the order:
                # the courier is not asked again.
                order.metadata["cancellation"]["shipment_cancel"] = (
                    shipment_cancel
                )
            else:
                # The courier is asked once this transaction has
                # committed. The flag is the durable half of that: it
                # commits with the cancel and goes with the answer.
                order.metadata[SHIPMENT_CANCEL_PENDING_FLAG] = True

            # An order never confirmed was never announced (a card order
            # nobody paid): its customer was told nothing about it, so its
            # cancel is not announced either, whoever cancels it. The
            # stock, the reservations and the payment are settled as for
            # any cancel.
            if order.confirmed_at is None:
                cls.suppress_customer_status_notifications(
                    order, OrderStatus.CANCELED.value
                )

            # A courier order's receipt not yet handed to the customer is
            # set aside to be canceled in myDATA, here, under this lock and
            # before the save: credits are created under the lock, and the
            # save is what asks for them (``request_credit_documents``), so
            # none can be created between the cancel and the hold; the
            # receipt's email reads the hold before it sends.
            # ``order_canceled``, sent after the commit, settles it
            # (``settle_canceled_receipt``).
            hold_receipt_for_cancel(order)
            order.save(
                update_fields=["status", "status_updated_at", "metadata"]
            )

            # order_canceled signal is dispatched by
            # handle_order_status_changed via the post_save chain.
            # Do not send it manually here to avoid double-firing.

            logger.info(
                "Order %s canceled successfully (previous status: %s)",
                order.id,
                old_status,
            )

            # No courier call here: this transaction holds the order's
            # row lock and the locks of the products it restocked.
            # ``cancel_order`` asks the courier after the commit.
            return order

        except Exception as e:
            logger.exception("Error canceling order %s", order.id)
            raise OrderCancellationError(
                order_id=order.id,
                reason=_("Failed to cancel order: {error}").format(
                    error=str(e)
                ),
            ) from e

    @classmethod
    def cancel_for_dead_shipment(
        cls, order: Order, *, carrier: str, silent_for_customer: bool
    ) -> None:
        """Cancel an order whose shipment was canceled before it left.

        A carrier sync that finds the shipment CANCELED (ACS: the admin
        retire action or a voucher cancel; a BoxNow ``canceled`` parcel
        leaves its order open, ``order.lifecycle.follow_up``) is looking at a
        parcel that never shipped. Such an order
        is not "status CANCELED", it is a cancellation: stock goes back
        on the shelf, the reservation is released, the payment settled,
        the history and signals fired — everything :meth:`cancel_order`
        owns. A bare status write flipped the label and stranded the
        stock (prod order 248, retired 2026-09-13, still PROCESSING a
        week later with its unit still counted as sold).

        The refund is left to a human: nobody asked for this cancel, and
        on a replay the event is historic — moving money without someone
        looking is worse than one WARNING.

        The courier is not asked to cancel: the shipment already is, and
        the caller is a tracking sync holding the shipment's row lock, so
        a call to the courier here would run inside its transaction.

        Raises ``OrderNotCancelableError`` when the order's status no
        longer allows a cancel, and ``OrderCancellationError`` when the
        cancel failed on the way and is worth another attempt.
        """
        if silent_for_customer:
            cls.suppress_customer_status_notifications(
                order, OrderStatus.CANCELED.value
            )
        if order.is_paid:
            logger.warning(
                "%s shipment canceled for a PAID order=%s — canceling "
                "without a refund; refund it by hand.",
                carrier,
                order.id,
            )
        cls.cancel_order(
            order,
            reason=f"{carrier} shipment canceled before dispatch",
            refund_payment=False,
            customer_initiated=False,
            shipment_already_canceled=True,
        )

    @classmethod
    def cancel_attached_shipment(cls, order: Order, reason: str) -> bool:
        """Best-effort: cancel the courier voucher of a canceled order.

        The staff / system half of :meth:`cancel_order`, the only path
        that cancels an order, so every cancel reaches the carrier. A
        customer cancel asks the courier first
        (:meth:`_ask_courier_to_cancel`) and is refused when the courier
        refuses.

        **Runs with no transaction open**, after the cancel committed:
        ``cancel_order`` registers it with ``transaction.on_commit``, and
        ``order.tasks.finish_pending_shipment_cancels`` runs it for an
        order whose worker stopped before it got here
        (:data:`SHIPMENT_CANCEL_PENDING_FLAG`). The carrier cancels are
        three-phase (lock, release, call, lock again), which only holds
        when nothing around them keeps a transaction open: called inside
        the cancel's own, every row that cancel locked stayed locked
        across the courier's HTTP round trip, and the shipment was
        locked after the order (#713, #613).

        Routed through ``ShippingService.cancel_shipment`` so each
        carrier enforces its own cancellability rules. Common
        rejections (ACS voucher already in a pickup list, BoxNow
        parcel already accepted at a locker) are recorded on
        ``order.metadata['cancellation']['shipment_cancel']`` so the
        admin can see why the cascade didn't reach the courier and
        coordinate the in-transit return out of band. A refusal is also
        left as a staff-only ``OrderHistory`` note. *order* gets the
        metadata as it was written.

        **"Already running" is not an answer.** A
        ``CarrierBusyError`` is our own refusal: another cancel of this
        voucher holds its claim (the sweep beside a first attempt still
        waiting for the courier, two runs of the sweep), or a creation
        is in flight or still settling. Nothing was asked of the
        courier, so nothing is recorded and the flag stays: the call
        that is running records what the courier says, or the sweep
        asks again once the claim is gone. Recording it would keep "a
        cancel is already in progress" as the courier's answer, with a
        staff note, while that other cancel succeeds.

        **No answer is not an answer either, for a while.** A
        ``CarrierUnavailableError`` (the courier unreachable, timed out,
        failing on its side, refusing our credentials) says nothing about
        the parcel. Until ``ORDER_SHIPMENT_CANCEL_RETRY_HOURS`` after the
        cancel the flag stays, the attempt is counted in
        :data:`SHIPMENT_CANCEL_UNANSWERED_KEY`, staff get one history note
        saying the parcel is still live (a courier that is down for us
        may still collect it) and that the courier is being asked again,
        and the sweep asks on each run. After that the last failure is recorded as the answer,
        with the usual note, and the parcel is staff's. A courier's
        refusal is recorded at once: asking again gets the same answer.

        Returns True when the courier gave no answer, whether the cancel
        is left for the sweep to ask again or its retries are over; False
        otherwise. The sweep asks that courier about no other order in
        its run either way.

        Never raises — the order is canceled whatever the courier, or
        the write of its answer, does. An answer that could not be
        written leaves the flag, so the sweep asks again; both carriers'
        cancel is a no-op on a shipment already canceled.
        """
        from shipping.services import ShippingService

        # A customer cancel asked the courier before the order moved and
        # recorded the answer: the courier is not asked a second time.
        # With no flag there is nothing to do; a flag beside an answer
        # (a writer that put back a copy loaded before the answer) is
        # removed below, or the sweep would take the order on every run.
        metadata = order.metadata or {}
        answered = "shipment_cancel" in (metadata.get("cancellation") or {})
        if answered and SHIPMENT_CANCEL_PENDING_FLAG not in metadata:
            return False

        info: dict[str, Any] | None = None
        no_answer = False
        if not answered:
            logger.info(
                "Cascading order cancel to carrier voucher | order=%s "
                "reason=%r",
                order.id,
                reason,
            )
            try:
                dispatched = ShippingService.cancel_shipment(
                    order, reason=reason
                )
                info = {
                    "attempted": True,
                    "dispatched": dispatched,
                }
            except CarrierBusyError as exc:
                logger.info(
                    "Order %s canceled; its shipment is already being "
                    "worked on, so the courier was not asked and the "
                    "cancel stays pending: %s",
                    order.id,
                    exc,
                )
                return False
            except CarrierUnavailableError as exc:
                if timezone.now() < shipment_cancel_retry_until(
                    order.status_updated_at
                ):
                    cls._note_unanswered_shipment_cancel(order, exc)
                    return True
                no_answer = True
                info = {
                    "attempted": True,
                    "dispatched": False,
                    "error": str(exc),
                }
                logger.warning(
                    "Order %s canceled, and its courier gave no answer to "
                    "the voucher cancel for %s hours; staff take it over: "
                    "%s",
                    order.id,
                    settings.ORDER_SHIPMENT_CANCEL_RETRY_HOURS,
                    exc,
                    exc_info=True,
                )
            except Exception as exc:  # pragma: no cover - logged below
                info = {
                    "attempted": True,
                    "dispatched": False,
                    "error": str(exc),
                }
                logger.warning(
                    "Order %s canceled, but courier voucher cancel failed: %s",
                    order.id,
                    exc,
                    exc_info=True,
                )

        try:
            recorded = cls._record_shipment_cancel(order, info)
            if recorded and info is not None and "error" in info:
                # The carrier outcome belongs where staff read about the
                # order, not only in a metadata blob: the order is
                # canceled (and refunded) while the parcel may still be
                # delivered.
                from order.models.history import OrderHistory

                OrderHistory.log_note(
                    order=order,
                    note=(
                        "Order canceled, but the courier did not cancel "
                        f"the parcel: {info['error']}. The parcel may "
                        "still be delivered; arrange its return with the "
                        "courier."
                    ),
                )
        except Exception:
            logger.exception(
                "Order %s is canceled, but the courier's answer to the "
                "shipment cancel (%s) could not be recorded; the "
                "pending-cancel sweep asks again",
                order.id,
                info,
            )
        return no_answer

    @classmethod
    def _note_unanswered_shipment_cancel(
        cls, order: Order, exc: CarrierUnavailableError
    ) -> None:
        """Count an attempt at a pending shipment cancel that the courier
        gave no answer to, and tell staff the first time.

        Written under the order's row lock, merged into the metadata as it
        is then (as :meth:`_record_shipment_cancel`), and only while the
        cancel is still pending with no answer: a run that recorded an
        answer meanwhile wins. *order* gets the metadata as written.
        Never raises: the count is for staff, and the sweep asks again
        with or without it.
        """
        try:
            with transaction.atomic():
                metadata: dict[str, Any] = (
                    Order.objects.select_for_update()
                    .filter(pk=order.pk)
                    .values_list("metadata", flat=True)
                    .get()
                ) or {}
                answered = "shipment_cancel" in (
                    metadata.get("cancellation") or {}
                )
                if answered or SHIPMENT_CANCEL_PENDING_FLAG not in metadata:
                    order.metadata = metadata
                    return
                previous = metadata.get(SHIPMENT_CANCEL_UNANSWERED_KEY) or {}
                attempts = int(previous.get("attempts", 0)) + 1
                metadata[SHIPMENT_CANCEL_UNANSWERED_KEY] = {
                    "since": previous.get("since")
                    or timezone.now().isoformat(),
                    "attempts": attempts,
                    "last_error": str(exc),
                }
                Order.objects.filter(pk=order.pk).update(metadata=metadata)
                order.metadata = metadata
                logger.warning(
                    "Order %s canceled, but its courier gave no answer to "
                    "the voucher cancel (attempt %s); asking again until "
                    "%s: %s",
                    order.id,
                    attempts,
                    shipment_cancel_retry_until(
                        order.status_updated_at
                    ).isoformat(),
                    exc,
                )
                if attempts == 1:
                    from order.models.history import OrderHistory

                    every = settings.ORDER_SHIPMENT_CANCEL_SWEEP_EVERY_MINUTES
                    hours = settings.ORDER_SHIPMENT_CANCEL_RETRY_HOURS
                    OrderHistory.log_note(
                        order=order,
                        note=(
                            "Order canceled, but the courier gave no "
                            f"answer to the parcel cancel: {exc}. The "
                            "parcel is still live and can be picked up "
                            "meanwhile: call the courier if it must not "
                            "ship. The cancel is also asked again "
                            f"automatically every {every} minutes for up "
                            f"to {hours} hours; a note follows here if it "
                            "never answers."
                        ),
                    )
        except Exception:
            logger.exception(
                "Order %s is canceled and its courier gave no answer to "
                "the shipment cancel (%s); the attempt could not be "
                "counted, and the pending-cancel sweep asks again",
                order.id,
                exc,
            )

    @staticmethod
    def _record_shipment_cancel(
        order: Order, info: dict[str, Any] | None
    ) -> bool:
        """Write the courier's answer on the order row as it is now.

        No lock was held while the courier answered, so the order may
        have changed: the metadata is read again under the row lock and
        the answer merged into it, never written from the copy the
        caller loaded. Returns False, and keeps what is there, when an
        answer is already recorded (another run got here first), and
        when there is none to record (*info* is ``None``).

        The pending flag, and the count of unanswered attempts beside it,
        go in the same write, and only when the row then holds an answer:
        a row with a flag and no answer is still to be asked about.

        ``.update(...)`` writes the one column that changed and runs no
        ``post_save`` receiver.
        """
        with transaction.atomic():
            metadata: dict[str, Any] = (
                Order.objects.select_for_update()
                .filter(pk=order.pk)
                .values_list("metadata", flat=True)
                .get()
            ) or {}
            answered = "shipment_cancel" in (metadata.get("cancellation") or {})
            recorded = info is not None and not answered
            if recorded:
                metadata.setdefault("cancellation", {})["shipment_cancel"] = (
                    info
                )
            stale_flag = answered and SHIPMENT_CANCEL_PENDING_FLAG in metadata
            if recorded or stale_flag:
                metadata.pop(SHIPMENT_CANCEL_PENDING_FLAG, None)
                metadata.pop(SHIPMENT_CANCEL_UNANSWERED_KEY, None)
                Order.objects.filter(pk=order.pk).update(metadata=metadata)
        order.metadata = metadata
        if recorded:
            # Once the answer is written: nothing a receiver does can undo it.
            order_shipment_cancel_answered.send(sender=Order, order=order)
        return recorded
