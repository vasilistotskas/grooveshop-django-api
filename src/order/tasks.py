import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone
from redis.exceptions import LockError

from core import celery_app
from core.retention import delete_in_batches
from core.retry import (
    MAIL_TRANSIENT_ERRORS,
    TRANSIENT_DB_ERRORS,
    TRANSIENT_ERRORS,
    is_permanent_failure,
)
from core.store_settings import store_setting
from core.tasks import MonitoredTask
from emails.send import send_email, was_sent
from emails.transport import Attachment
from order.communications.review_request import (
    awaiting_review_request,
    delivery_window,
)
from order.emails import (
    STATUS_EMAIL_KEYS,
    OrderDispute,
    is_paid_online,
    suggestion_strip_of,
    unreviewed_products,
)
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import (
    CHARGED_PAYMENT_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.models import (
    Order,
    OrderHistory,
    PaymentTransaction,
    PendingPaymentFee,
    RefundNotification,
)
from order.models.order import (
    status_update_reservation_key,
)
from order.payments import payment_hold
from order.payments.payment import get_payment_provider_class
from order.refunds import refund_reconciliation
from order.services import (
    SHIPMENT_CANCEL_PENDING_FLAG,
    OrderService,
    shipment_cancel_retry_until,
)
from recommendation.order_email import record_impression
from tenant.celery import dispatch_on_commit

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# send_order_confirmation_email idempotency
#
# Design: two-layer guard to survive both concurrent fires AND
# worker OOM-kills mid-send.
#
# Layer 1 — permanent DB timestamp (``confirmation_email_sent_at``):
#   Written once after a successful send. Any retry that finds this
#   key set skips immediately without touching Redis.
#
# Layer 2 — Redis execution lock (``CONFIRMATION_EMAIL_LOCK_TTL`` TTL):
#   Acquired via cache.add() (atomic "set-if-not-exists") before the
#   send attempt. Prevents concurrent workers from both sending when
#   the DB flag has not yet been written (the window between "lock
#   acquired" and "DB write committed"). If the worker is OOM-killed
#   mid-send the lock auto-expires and the next retry can claim it.
#
# Contrast with the old pattern (boolean flag set at reserve time):
#   Old: flag set → worker killed → flag stays True → customer never
#         receives confirmation.
#   New: lock acquired → worker killed → lock expires in 60 s →
#         next retry claims lock → send succeeds → DB flag written.
# ──────────────────────────────────────────────────────────────
CONFIRMATION_EMAIL_SENT_AT_KEY = "confirmation_email_sent_at"
CONFIRMATION_EMAIL_LOCK_PREFIX = "order:confirm_email_lock:"
# TTL for the execution lock. Long enough for a slow SMTP send; short
# enough that a dead worker's lock expires quickly so a retry can proceed.
CONFIRMATION_EMAIL_LOCK_TTL = 90  # seconds


def _confirmation_lock_key(order_id: int) -> str:
    return f"{CONFIRMATION_EMAIL_LOCK_PREFIX}{order_id}"


def _confirmation_already_sent(metadata: dict | None) -> bool:
    """Return True if the permanent DB timestamp shows the email was sent."""
    return bool((metadata or {}).get(CONFIRMATION_EMAIL_SENT_AT_KEY))


def _mark_confirmation_sent(order_id: int) -> None:
    """Write the permanent sent-at timestamp and release the Redis lock."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=order_id).first()
        if order is None:
            return
        if not order.metadata:
            order.metadata = {}
        order.metadata[CONFIRMATION_EMAIL_SENT_AT_KEY] = (
            timezone.now().isoformat()
        )
        order.save(update_fields=["metadata"])
    # Release the execution lock immediately after the DB commit so the
    # next accidental duplicate call sees the DB flag instead of waiting
    # for the TTL.
    cache.delete(_confirmation_lock_key(order_id))


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_order_confirmation_email(self, order_id: int) -> bool:
    """Send the order confirmation email exactly once per order.

    Idempotency design (worker-kill safe):
    - Permanent guard: ``confirmation_email_sent_at`` DB timestamp. Once
      set, no further sends occur regardless of how many times the task
      is called.
    - Execution lock: Redis key with a short TTL. Prevents concurrent
      workers from both sending while the DB flag is not yet written.
      If the worker is OOM-killed mid-send the lock auto-expires so the
      next retry can proceed — this is the fix for the bug where the old
      boolean flag (set before the send) would permanently block resends
      after a worker kill.
    """
    try:
        # Fast path: check permanent DB flag before touching Redis.
        # Use .values() to avoid triggering Order.__init__'s lazy-loading
        # of deferred fields — .only() causes infinite recursion via the
        # _original_tracking_number descriptor on Order.__init__.
        meta_row = Order.objects.filter(id=order_id).values("metadata").first()
        if meta_row is None:
            logger.error(
                "Could not send confirmation email - Order #%s not found",
                order_id,
                extra={"order_id": order_id},
            )
            return False

        if _confirmation_already_sent(meta_row["metadata"]):
            logger.info(
                "Order confirmation email already sent for order #%s, skipping",
                order_id,
            )
            return True

        # Try to acquire the execution lock. cache.add() is atomic:
        # returns True only if the key did not already exist.
        lock_key = _confirmation_lock_key(order_id)
        lock_acquired = cache.add(lock_key, "1", CONFIRMATION_EMAIL_LOCK_TTL)
        if not lock_acquired:
            # Another worker is currently in the send window. Log and
            # bail — either that worker will succeed and set the permanent
            # flag, or it will die and the lock will expire.
            logger.info(
                "Order #%s confirmation email send already in progress "
                "(execution lock held), skipping",
                order_id,
            )
            return True

        # We hold the lock. Fetch the full order for rendering.
        order = (
            Order.objects.select_related("user", "country", "region", "pay_way")
            .prefetch_related("pay_way__translations")
            .get(id=order_id)
        )

        # Double-check permanent flag under DB read — another worker may
        # have committed the sent_at timestamp while we were fetching.
        if _confirmation_already_sent(order.metadata):
            cache.delete(lock_key)
            logger.info(
                "Order confirmation email already sent for order #%s "
                "(post-lock check), skipping",
                order_id,
            )
            return True

        email_type = (
            "order.payment_confirmed"
            if is_paid_online(order)
            else "order.received"
        )
        deliveries = send_email(email_type, order)
        if not was_sent(deliveries):
            # The store's switch or a gate turned the mail off: nothing
            # went out, so no flag is written (a later call may send) and
            # the lock is given back.
            cache.delete(lock_key)
            logger.info(
                "Order #%s confirmation email (%s) not sent: %s",
                order_id,
                email_type,
                deliveries[0].status,
                extra={"order_id": order_id, "email_type": email_type},
            )
            return True
        [delivery] = deliveries
        subject = delivery.subject

        # Write permanent flag + release lock atomically (DB commit, then
        # cache.delete). From this point on every future call to this task
        # will short-circuit on the DB flag.
        _mark_confirmation_sent(order_id)

        suggestion_strip = suggestion_strip_of(order)
        if suggestion_strip is not None:
            record_impression(order, suggestion_strip)

        # The rendered subject goes into the log AND the order history:
        # it is the one thing the customer quotes back ("I got an email
        # saying delivered"), and it is what the translation overlay
        # can silently change underneath the template.
        logger.info(
            "Order confirmation email sent for order #%s (%s): %r",
            order.id,
            email_type,
            subject,
            extra={
                "order_id": order.id,
                "email": order.email,
                "email_type": email_type,
                "subject": subject,
            },
        )

        OrderHistory.log_note(
            order=order,
            note=f"Order confirmation email sent to {order.email} — "
            f"subject: {subject}",
        )

        return True

    except Order.DoesNotExist:
        logger.error(
            "Could not send confirmation email - Order #%s not found",
            order_id,
            extra={"order_id": order_id},
        )
        return False

    except Exception as e:
        logger.error(
            "Error sending order confirmation email for order #%s: %s",
            order_id,
            e,
            extra={"order_id": order_id, "error": str(e)},
        )

        retry_count = self.request.retries
        max_retries = self.max_retries

        if retry_count < max_retries:
            logger.info(
                "Retrying send_order_confirmation_email for order #%s "
                "(attempt %s/%s)",
                order_id,
                retry_count + 1,
                max_retries + 1,
            )
            raise self.retry(exc=e) from e

        # Permanent failure: release the lock so an admin can trigger a
        # manual resend. The DB flag is NOT set — the email was never sent.
        cache.delete(_confirmation_lock_key(order_id))
        return False


def _reserve_status_update_email(order_id: int, new_status: str) -> bool:
    """Atomically claim the status-update-email slot for (order, status).

    Returns True if this caller won the race and should proceed with the
    send, False if another caller has already sent (or is sending) the
    email. The reservation includes BOTH order_id AND new_status so
    different status transitions remain independent.
    Raises Order.DoesNotExist if the order is missing.
    """
    flag_key = status_update_reservation_key(order_id, new_status)
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.metadata and order.metadata.get(flag_key):
            return False
        if not order.metadata:
            order.metadata = {}
        order.metadata[flag_key] = True
        order.save(update_fields=["metadata"])
    return True


def _release_status_update_email(order_id: int, new_status: str) -> None:
    """Clear the status-update-email reservation on permanent failure."""
    flag_key = status_update_reservation_key(order_id, new_status)
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=order_id).first()
        if order is None or not order.metadata:
            return
        if order.metadata.pop(flag_key, None) is not None:
            order.save(update_fields=["metadata"])


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_dispute_notification_email(
    self, order_id: int, dispute_id: str, provider_code: str, lost: bool = False
) -> bool:
    """Notify staff that a *provider_code* dispute has been opened for an
    order, or (``lost``) that it closed against the merchant and the money
    was recorded as a chargeback.

    Sends to the staff group email configured via ``INFO_EMAIL``.  Does NOT
    change order status — that is a manual decision.  Includes structured
    log fields so ops can correlate the Celery log line with the provider
    dashboard entry.
    """
    try:
        order = Order.objects.select_related("user", "pay_way").get(id=order_id)

        # Staff-facing: the pipeline renders in the store's own language.
        deliveries = send_email(
            "team.dispute_lost" if lost else "team.dispute_opened",
            OrderDispute(
                order=order, dispute_id=dispute_id, provider_code=provider_code
            ),
        )
        if not was_sent(deliveries):
            logger.warning(
                "send_dispute_notification_email: not sent (%s) — skipping",
                deliveries[0].status,
                extra={"order_id": order_id, "dispute_id": dispute_id},
            )
            return False

        logger.info(
            "Dispute notification email sent for order #%s (dispute=%s)",
            order_id,
            dispute_id,
            extra={"order_id": order_id, "dispute_id": dispute_id},
        )
        return True

    except Order.DoesNotExist:
        logger.error(
            "Could not send dispute notification — Order #%s not found",
            order_id,
            extra={"order_id": order_id, "dispute_id": dispute_id},
        )
        return False


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_admin_new_order_email(self, order_id: int) -> bool:
    """Notify the store operator that a new order has been placed.

    This is a MERCHANT-facing alert, not a platform one: it goes to the
    store's operators (``emails.recipients.store_operators``: its contact
    and owner addresses) FROM the store's sender, so each store's operator
    gets their own orders (NOT ``settings.ADMINS``, which is the SaaS
    platform's admin list shared across all tenants). A store with neither
    address, or one that switched the alert off, sends nothing and the task
    returns ``False``.
    """
    try:
        order = (
            Order.objects.select_related("user", "country", "region", "pay_way")
            .prefetch_related("pay_way__translations")
            .get(id=order_id)
        )

        # Merchant-facing: the pipeline renders in the store's own language.
        deliveries = send_email("team.new_order", order)
        if not was_sent(deliveries):
            logger.warning(
                "send_admin_new_order_email: not sent (%s) — skipping",
                deliveries[0].status,
                extra={"order_id": order_id},
            )
            return False

        logger.info(
            "Merchant new-order email sent for order #%s",
            order_id,
            extra={"order_id": order_id},
        )
        return True

    except Order.DoesNotExist:
        logger.error(
            "Could not send merchant new-order email — Order #%s not found",
            order_id,
            extra={"order_id": order_id},
        )
        return False


PAYMENT_FAILED_EMAIL_SENT_FLAG = "payment_failed_email_sent"


def _reserve_payment_failed_email(order_id: int) -> bool:
    """Atomically claim the payment-failed-email slot for an order.

    Reserve-before-send, so concurrent webhook
    deliveries (Stripe + Viva, or retries) cannot both send the email.
    Returns True if this caller won the race and should proceed,
    False if another caller already claimed it. Raises
    Order.DoesNotExist if the order is missing.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.metadata and order.metadata.get(
            PAYMENT_FAILED_EMAIL_SENT_FLAG
        ):
            return False
        if not order.metadata:
            order.metadata = {}
        order.metadata[PAYMENT_FAILED_EMAIL_SENT_FLAG] = True
        order.save(update_fields=["metadata"])
    return True


def _release_payment_failed_email(order_id: int) -> None:
    """Release the payment-failed-email reservation on permanent send failure."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=order_id).first()
        if order is None or not order.metadata:
            return
        if order.metadata.pop(PAYMENT_FAILED_EMAIL_SENT_FLAG, None) is not None:
            order.save(update_fields=["metadata"])


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_payment_failed_email(self, order_id: int) -> bool:
    """Notify the customer that their payment failed.

    Idempotent via metadata flag (reserve-before-send). Includes a
    retry URL so the customer can attempt the payment again without
    starting a new order.
    """
    email_sent = False
    try:
        if (self.request.retries == 0) and (
            not _reserve_payment_failed_email(order_id)
        ):
            logger.info(
                "Payment failed email already sent for order #%s, skipping",
                order_id,
            )
            return True

        # No item prefetch: this email neither passes ``items`` into the
        # context nor renders a line table.
        order = Order.objects.select_related("user", "pay_way").get(id=order_id)

        # A provider can charge the order between the failure and this run
        # (a Viva retry that succeeds), and the email says nothing was
        # charged. A canceled payment took nothing, so it still sends.
        if order.payment_status in CHARGED_PAYMENT_STATUSES:
            logger.info(
                "Payment failed email for order #%s not sent: payment %s",
                order_id,
                order.payment_status,
            )
            return True

        if not was_sent(send_email("order.payment_failed", order)):
            logger.info(
                "Payment failed email for order #%s not sent: turned off",
                order_id,
            )
            return True
        email_sent = True

        OrderHistory.log_note(
            order=order,
            note=f"Payment failed email sent to {order.email}",
        )

        logger.info(
            "Payment failed email sent for order #%s",
            order.id,
            extra={"order_id": order.id, "email": order.email},
        )
        return True

    except Order.DoesNotExist:
        logger.error(
            f"Could not send payment-failed email - Order #{order_id} not found",
            extra={"order_id": order_id},
        )
        return False

    except Exception as e:
        logger.error(
            f"Error sending payment-failed email for order #{order_id}: {e!s}",
            extra={"order_id": order_id, "error": str(e)},
        )
        if email_sent:
            # The message is already with the relay. Whatever failed
            # after it (the history note, the logging call) must not put
            # the customer through a second payment-failed email — the
            # reservation is only consulted on the first attempt, so a
            # retry here would send again, up to max_retries times.
            logger.error(
                "payment-failed email for order #%s was sent but the bookkeeping "
                "after it failed: %s",
                order_id,
                e,
                extra={"order_id": order_id, "error": str(e)},
            )
            return True

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        # Unconditionally, NOT "if this attempt reserved": the slot is
        # claimed on attempt 0, which always retries while attempts
        # remain, so by the time control reaches here the claim was made
        # by an earlier attempt of this same run and a per-attempt flag
        # is always False. Guarding on one made the release dead code and
        # left the flag standing for a send that never happened.
        _release_payment_failed_email(order_id)
        return False


def _claim_refund_email(refund: PaymentTransaction) -> bool:
    """Claim the confirmation email of *refund*, once.

    Reserve-before-send, like the payment-failed email: the claim is the
    insert of a ``RefundNotification`` row, whose one-to-one on the refund
    makes the database decide between two tasks handed the same refund (a
    delivery repeated by the broker, a retry racing its original). Only the
    caller whose insert won gets ``True`` and sends; the other finds the
    row and stands down.
    """
    _notification, claimed = RefundNotification.objects.get_or_create(
        refund=refund
    )
    return claimed


def _release_refund_email(refund_id: int) -> None:
    """Give the claim back once the send has permanently failed, so the
    record does not say the customer was told when they were not."""
    RefundNotification.objects.filter(refund_id=refund_id).delete()


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_refund_confirmation_email(self, refund_id: int) -> bool:
    """Tell the customer about ONE refund: a ``PaymentTransaction`` REFUND.

    Triggered from ``handle_order_refunded``, which queues one task per
    refund the ledger takes, so every refund path converges here — the
    in-app refund (``OrderService.refund_order``), the staff Refund
    action, and dashboard refunds arriving as ``charge.refunded`` / Viva
    reversal webhooks. An order refunded in parts therefore sends one
    email per part, each claimed on its own row (``_claim_refund_email``):
    a task delivered twice sends once, and one refund's task cannot
    swallow another's email. A refund the ledger already holds never
    signals again, so there is no second email for the webhook that
    reports a refund this app made.

    The claim is made before the send, so a worker that dies between the
    two leaves the refund claimed and unsent: the price of never sending
    it twice. A send that fails for good releases the claim.

    Sends the ``order.refunded`` email type, which renders
    ``emails/order/order_refunded.html`` (it builds on
    ``emails/base/order_status.html``'s REFUNDED branch).
    """
    email_sent = False
    try:
        refund = PaymentTransaction.objects.select_related(
            "order__user", "order__country", "order__region", "order__pay_way"
        ).get(pk=refund_id, kind=PaymentTransactionKind.REFUND)
        order = refund.order
        # Every attempt claims, but only the first stands down when the
        # claim is not won: a retry may be holding the very claim an earlier
        # attempt of this run made.
        claimed = _claim_refund_email(refund)
        if not claimed and self.request.retries == 0:
            logger.info(
                "Refund confirmation email already claimed for refund #%s "
                "of order #%s, skipping",
                refund_id,
                order.id,
            )
            return True

        if not was_sent(send_email("order.refunded", refund)):
            logger.info(
                "Refund confirmation email for refund #%s not sent: turned off",
                refund_id,
            )
            return True
        email_sent = True

        OrderHistory.log_note(
            order=order,
            note=(
                f"Refund confirmation email sent to {order.email} "
                f"({refund.amount})"
            ),
        )

        logger.info(
            "Refund confirmation email sent for refund #%s of order #%s",
            refund_id,
            order.id,
            extra={"order_id": order.id, "email": order.email},
        )
        return True

    except PaymentTransaction.DoesNotExist:
        logger.error(
            "Could not send refund confirmation email — refund #%s not found",
            refund_id,
            extra={"refund_id": refund_id},
        )
        return False

    except Exception as e:
        logger.error(
            "Error sending refund confirmation email for refund #%s: %s",
            refund_id,
            e,
            extra={"refund_id": refund_id, "error": str(e)},
        )
        if email_sent:
            # The message is already with the relay. Whatever failed
            # after it (the history note, the logging call) must not put
            # the customer through a second refund confirmation email: a
            # retry here would send again, up to max_retries times.
            logger.error(
                "refund confirmation email for refund #%s was sent but the "
                "bookkeeping after it failed: %s",
                refund_id,
                e,
                extra={"refund_id": refund_id, "error": str(e)},
            )
            return True

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        # Out of attempts: give the claim back, so the refund is not
        # recorded as told when it was not, and a later dispatch can send.
        _release_refund_email(refund_id)
        return False


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    retry_backoff=True,
    retry_jitter=True,
)
def send_order_status_update_email(
    self, order_id: int, status: OrderStatus
) -> bool:
    """Send a status-update email for an order.

    Idempotent via a per-(order, status) metadata reservation so concurrent
    retries and signal re-fires cannot double-send for the same transition.
    On permanent failure the reservation is released so an admin can
    trigger a manual resend.
    """
    email_sent = False
    try:
        # Only reserve on the first attempt to prevent the flag from
        # blocking legitimate retries after a transient failure.
        if (self.request.retries == 0) and (
            not _reserve_status_update_email(order_id, status)
        ):
            logger.info(
                "Status update email already sent (or reserved) "
                "for order #%s status=%s, skipping",
                order_id,
                status,
            )
            return True

        order = Order.objects.select_related(
            "user", "country", "region", "pay_way"
        ).get(id=order_id)

        # Internal-only milestones never get their own customer email:
        #   • PENDING — covered by the order-received confirmation.
        #   • PROCESSING — an internal "we're preparing it" step,
        #     covered by order-received (offline) or the payment-
        #     confirmed notification (online). Surfacing it as its own
        #     email produced a duplicate right after the confirmation.
        #   • SHIPPED — owned by the dedicated
        #     ``send_shipping_notification_email`` task, which carries
        #     the tracking number and only fires once the parcel is
        #     genuinely in transit. Routing it through the generic
        #     status template here would double-send.
        if status in (
            OrderStatus.PENDING,
            OrderStatus.PENDING.value,
            OrderStatus.PROCESSING,
            OrderStatus.PROCESSING.value,
            OrderStatus.SHIPPED,
            OrderStatus.SHIPPED.value,
        ):
            return True

        email_type = STATUS_EMAIL_KEYS[status]
        deliveries = send_email(email_type, order)
        if not was_sent(deliveries):
            # The store switched this status mail off (delivered and
            # completed can be): the reservation stays, so a re-fired
            # signal does not ask again.
            logger.info(
                "Status update email for order #%s status=%s not sent: %s",
                order_id,
                status,
                deliveries[0].status,
            )
            return True
        email_sent = True
        [delivery] = deliveries
        subject = delivery.subject

        logger.info(
            "Order status update email sent for order #%s - Status: %s "
            "(%s): %r",
            order.id,
            status,
            email_type,
            subject,
            extra={
                "order_id": order.id,
                "status": status,
                "email": order.email,
                "email_type": email_type,
                "subject": subject,
            },
        )

        OrderHistory.log_note(
            order=order,
            note=f"Status update email sent to {order.email} for {status} "
            f"status — subject: {subject}",
        )

        return True

    except Order.DoesNotExist:
        logger.error(
            f"Could not send status update email - Order #{order_id} not found",
            extra={"order_id": order_id, "status": status},
        )
        return False

    except Exception as e:
        logger.error(
            f"Error sending order status update email for order #{order_id}: {e!s}",
            extra={"order_id": order_id, "status": status, "error": str(e)},
        )

        if email_sent:
            # The message is already with the relay. Whatever failed
            # after it (the history note, the logging call) must not put
            # the customer through a second order status update email — the
            # reservation is only consulted on the first attempt, so a
            # retry here would send again, up to max_retries times.
            logger.error(
                "order status update email for order #%s was sent but the bookkeeping "
                "after it failed: %s",
                order_id,
                e,
                extra={"order_id": order_id, "error": str(e)},
            )
            return True

        if self.request.retries < self.max_retries:
            logger.info(
                f"Retrying send_order_status_update_email for order #{order_id} "
                f"(attempt {self.request.retries + 1}/{self.max_retries + 1})"
            )
            raise self.retry(exc=e) from e

        # Unconditionally, NOT "if this attempt reserved": the slot is
        # claimed on attempt 0, which always retries while attempts
        # remain, so by the time control reaches here the claim was made
        # by an earlier attempt of this same run and a per-attempt flag
        # is always False. Guarding on one made the release dead code and
        # left the flag standing for a send that never happened.
        _release_status_update_email(order_id, status)

        return False


SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG = "shipping_notification_email_sent"


def _reserve_shipping_notification_email(order_id: int) -> bool:
    """Atomically claim the shipping-notification-email slot for an order.

    Reserve-before-send, so concurrent fires of the
    ``order_shipment_dispatched`` signal (e.g. an admin manually
    re-saving tracking + a carrier event arriving in the same window)
    can't email the customer twice. Raises ``Order.DoesNotExist`` if
    the order is missing so the caller's logging path stays
    consistent with the rest of the file.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.metadata and order.metadata.get(
            SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG
        ):
            return False
        if not order.metadata:
            order.metadata = {}
        order.metadata[SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG] = True
        order.save(update_fields=["metadata"])
    return True


def _release_shipping_notification_email(order_id: int) -> None:
    """Clear the shipping-notification reservation on permanent failure.

    Without this the flag outlives the send it was standing in for: the
    four attempts span ~35 minutes, and if the relay is down for all of
    them the order keeps ``shipping_notification_email_sent`` forever.
    The customer is never told the parcel shipped and never gets the
    tracking number, and re-saving the tracking in the admin is
    short-circuited by the same flag. The deferral paths above already
    take care NOT to reserve for exactly this reason.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=order_id).first()
        if order is None or not order.metadata:
            return
        if (
            order.metadata.pop(SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG, None)
            is not None
        ):
            order.save(update_fields=["metadata"])


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_shipping_notification_email(self, order_id: int) -> bool:
    email_sent = False
    try:
        order = (
            Order.objects.select_related("user", "country", "region", "pay_way")
            .prefetch_related("items__product__translations")
            .get(id=order_id)
        )

        # "Your order has shipped" must stay honest: it only goes out
        # once the parcel is genuinely in transit (status SHIPPED) AND we
        # have a tracking number to show. This task is dispatched from
        # two events — the moment tracking lands
        # (``order_shipment_dispatched``, which on a COD/online order
        # fires at voucher-mint while the order is still PROCESSING) and
        # the moment the order reaches SHIPPED (``order_status_changed``).
        # Whichever fires first finds the other condition unmet and
        # defers; the second one sends. Deferrals return ``True``
        # (success, no-op) so Celery doesn't retry, and — crucially — we
        # DON'T reserve the idempotency flag on a deferral, or the real
        # send would be permanently blocked.
        if order.status != OrderStatus.SHIPPED.value:
            logger.debug(
                "Order #%s is %s, not SHIPPED yet — deferring shipped email",
                order_id,
                order.status,
            )
            return True

        if not order.tracking_number or not order.shipping_carrier:
            logger.warning(
                "Order #%s reached SHIPPED without tracking info — deferring "
                "shipped email until tracking lands",
                order_id,
                extra={"order_id": order_id},
            )
            return True

        # Only reserve on the first attempt. Retries are expected to
        # re-send because the flag is held by this worker; releasing
        # on every retry would defeat idempotency, and re-checking
        # would block a legitimate retry after a transient failure.
        if (self.request.retries == 0) and (
            not _reserve_shipping_notification_email(order_id)
        ):
            logger.info(
                "Shipping notification email already sent (or reserved) "
                "for order #%s, skipping",
                order_id,
            )
            return True

        if not was_sent(send_email("order.shipped", order)):
            logger.info(
                "Shipping notification email for order #%s not sent: "
                "turned off",
                order_id,
            )
            return True
        email_sent = True

        logger.info(
            f"Shipping confirmation email sent for order #{order.id}",
            extra={
                "order_id": order.id,
                "tracking_number": order.tracking_number,
                "carrier": order.shipping_carrier,
            },
        )

        OrderHistory.log_note(
            order=order,
            note=f"Shipping confirmation email sent to {order.email} with "
            f"tracking number {order.tracking_number} via {order.shipping_carrier}",
        )

        return True

    except Order.DoesNotExist:
        logger.error(
            f"Could not send shipping confirmation email - Order #{order_id} not found",
            extra={"order_id": order_id},
        )
        return False

    except Exception as e:
        logger.error(
            f"Error sending shipping confirmation email for order #{order_id}: {e!s}",
            extra={"order_id": order_id, "error": str(e)},
        )

        if email_sent:
            # The message is already with the relay. Whatever failed
            # after it (the history note, the logging call) must not put
            # the customer through a second shipping notification email — the
            # reservation is only consulted on the first attempt, so a
            # retry here would send again, up to max_retries times.
            logger.error(
                "shipping notification email for order #%s was sent but the bookkeeping "
                "after it failed: %s",
                order_id,
                e,
                extra={"order_id": order_id, "error": str(e)},
            )
            return True

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e

        # Unconditionally, NOT "if this attempt reserved": the slot is
        # claimed on attempt 0, which always retries while attempts
        # remain, so by the time control reaches here the claim was made
        # by an earlier attempt of this same run and a per-attempt flag
        # is always False. Guarding on one made the release dead code and
        # left the flag standing for a send that never happened.
        _release_shipping_notification_email(order_id)
        return False


#: ``Order.metadata`` key: the receipt waits for a refund claim to resolve.
RECEIPT_DEFERRED_FLAG = "receipt_deferred"


def set_receipt_deferred(order_id: int, deferred: bool) -> None:
    """Record, or clear, that the order's receipt waits for a refund claim
    still PENDING (``order.accounting.invoicing.ReceiptDeferred``)."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if (
            order is None
            or bool((order.metadata or {}).get(RECEIPT_DEFERRED_FLAG))
            == deferred
        ):
            return
        metadata = dict(order.metadata or {})
        if deferred:
            metadata[RECEIPT_DEFERRED_FLAG] = True
        else:
            metadata.pop(RECEIPT_DEFERRED_FLAG)
        order.metadata = metadata
        order.save(update_fields=["metadata"])


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def generate_order_invoice(self, order_id: int) -> bool:
    """Issue the order's retail receipt (myDATA 11.1): the row and its PDF,
    then the submission to AADE (``send_invoice_to_mydata``).

    Dispatched when a carrier's voucher or parcel is created, before the
    pickup (``issue_receipt_for_pickup``), when the order
    ships (``handle_order_shipped``, for an order no carrier takes), both
    while ``MyDataConfig.issues_automatically``, or by staff from the order.
    Exactly once per order: ``order.accounting.invoicing.generate_invoice`` returns
    the existing row, and an order whose receipt already carries a MARK
    has nothing left to issue. ``generate_invoice`` refuses an order that
    is not a shipped receipt order (an invoice order is issued by hand in
    timologio) and a store that issues outside the platform, whoever
    asked; that refusal is final, not retried. An order whose refunds are not all
    placed on the receipt yet (a claim still PENDING holds the room,
    ``ReceiptDeferred``) is flagged ``receipt_deferred``; the periodic
    ``allocate_credit_sources`` asks for the receipt again once they are.
    """
    from order.accounting.invoicing import (
        ReceiptDeferred,
        ReceiptNotIssuable,
        generate_invoice,
    )

    try:
        order = Order.objects.select_related(
            "country", "region", "pay_way"
        ).get(id=order_id)
    except Order.DoesNotExist:
        logger.error(
            "Could not issue receipt - Order #%s not found",
            order_id,
            extra={"order_id": order_id},
        )
        return False

    try:
        invoice = generate_invoice(order)
    except ReceiptDeferred as waiting:
        logger.info(
            "Receipt of order #%s waits: %s",
            order_id,
            waiting,
            extra={"order_id": order_id},
        )
        set_receipt_deferred(order_id, True)
        return False
    except ReceiptNotIssuable as refusal:
        logger.info(
            "No receipt issued for order #%s: %s",
            order_id,
            refusal,
            extra={"order_id": order_id},
        )
        # Not waiting for a refund any more: it is final (fully refunded,
        # myDATA off, not a receipt order), so nothing asks again.
        set_receipt_deferred(order_id, False)
        return False
    except Exception as e:
        logger.exception(
            "Error generating receipt for order #%s",
            order_id,
            extra={"order_id": order_id, "error": str(e)},
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        return False

    set_receipt_deferred(order_id, False)
    logger.info(
        "Receipt %s ready for order #%s", invoice.invoice_number, order_id
    )
    if not invoice.has_document():
        # A row without a file is resumed by the next retry or an admin
        # regeneration; there is nothing to register yet.
        return True
    if invoice.mydata_mark:
        return True

    dispatch_on_commit(send_invoice_to_mydata, [order_id])
    return True


INVOICE_EMAIL_SENT_FLAG = "invoice_email_sent"


def _credit_email_flag(credit_id: int) -> str:
    return f"credit_email_sent_{credit_id}"


def _reserve_document_email(order_id: int, flag: str) -> bool:
    """Reserve-before-send for a retail document's email.

    Returns ``True`` when this caller won the race. The flag lives
    under ``Order.metadata`` so it survives task retries and
    concurrent webhook bursts.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.metadata and order.metadata.get(flag):
            return False
        if not order.metadata:
            order.metadata = {}
        order.metadata[flag] = True
        order.save(update_fields=["metadata"])
    return True


def _release_document_email(order_id: int, flag: str) -> None:
    """Clear a document email's reservation on permanent failure."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=order_id).first()
        if order is None or not order.metadata:
            return
        if order.metadata.pop(flag, None) is not None:
            order.save(update_fields=["metadata"])


def _send_document_email(document, *, is_credit: bool) -> bool:
    """Email the document's PDF to the buyer; whether it went out."""
    # ``storage.open`` works for both S3 and FileSystem — streams
    # from S3 in prod, opens the file in dev. ``.read()`` is fine
    # at these sizes (≤ ~50kB typical).
    with document.document_file.open("rb") as fh:
        pdf_bytes = fh.read()
    deliveries = send_email(
        "order.credit_issued" if is_credit else "order.receipt_issued",
        document,
        attachments=[
            Attachment(
                filename=f"{document.document_number}.pdf",
                content=pdf_bytes,
                mimetype="application/pdf",
            )
        ],
    )
    return was_sent(deliveries)


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_invoice_email(self, order_id: int) -> bool:
    """Email the registered retail receipt's PDF to the buyer.

    Chained from ``send_invoice_to_mydata`` once the MARK is persisted and
    the PDF carries it. Idempotent via ``INVOICE_EMAIL_SENT_FLAG`` in
    ``Order.metadata`` so a retry doesn't re-send. Released on permanent
    failure so an admin can resend manually.
    """
    email_sent = False
    try:
        if (self.request.retries == 0) and (
            not _reserve_document_email(order_id, INVOICE_EMAIL_SENT_FLAG)
        ):
            logger.info(
                "Receipt email already sent for order #%s, skipping",
                order_id,
            )
            return True

        order = Order.objects.select_related(
            "user", "country", "region", "pay_way"
        ).get(id=order_id)
        invoice = getattr(order, "invoice", None)
        if invoice is None or not invoice.has_document():
            # A row with no PDF is recoverable, so retry rather than give
            # up on the first look: the registration clears the PDF
            # rendered before it (it prints no MARK) until its re-render
            # stores the registered one.
            if invoice is not None and self.request.retries < self.max_retries:
                logger.warning(
                    "Receipt PDF for order #%s is not ready — retrying",
                    order_id,
                )
                raise self.retry(countdown=self.default_retry_delay)

            _release_document_email(order_id, INVOICE_EMAIL_SENT_FLAG)
            logger.error(
                "Receipt email abandoned for order #%s — no PDF after %s "
                "attempt(s); the receipt needs regenerating by hand",
                order_id,
                self.request.retries + 1,
            )
            return False

        if not invoice.is_deliverable():
            # Read now, not when the email was queued: the receipt may wait
            # for its parcel (a canceled order's set aside under the
            # cancel's lock, a movement receipt before the pickup) or have
            # been canceled in myDATA since. Whoever makes it deliverable
            # queues the email again, so the reservation goes back.
            _release_document_email(order_id, INVOICE_EMAIL_SENT_FLAG)
            logger.info(
                "Receipt email for order #%s not sent: the receipt is not "
                "for the customer now",
                order_id,
            )
            return False

        if not _send_document_email(invoice, is_credit=False):
            logger.info(
                "Receipt email for order #%s not sent: turned off", order_id
            )
            return True
        email_sent = True

        logger.info(
            "Receipt email sent for order #%s (%s)",
            order.id,
            invoice.invoice_number,
        )
        OrderHistory.log_note(
            order=order,
            note=f"Receipt {invoice.invoice_number} emailed to {order.email}",
        )
        return True

    except Order.DoesNotExist:
        logger.error(
            "Could not send receipt email - Order #%s not found", order_id
        )
        return False

    except Exception as e:
        logger.exception(
            "Error sending receipt email for order #%s",
            order_id,
            extra={"order_id": order_id, "error": str(e)},
        )
        if email_sent:
            # The message is already with the relay. Whatever failed
            # after it (the history note, the logging call) must not put
            # the customer through a second email — the reservation is
            # only consulted on the first attempt, so a retry here would
            # send again, up to max_retries times.
            logger.error(
                "receipt email for order #%s was sent but the bookkeeping "
                "after it failed: %s",
                order_id,
                e,
                extra={"order_id": order_id, "error": str(e)},
            )
            return True

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        # Unconditionally, NOT "if this attempt reserved": the slot is
        # claimed on attempt 0, which always retries while attempts
        # remain, so a per-attempt flag is always False by now.
        _release_document_email(order_id, INVOICE_EMAIL_SENT_FLAG)
        return False


def _submit_document_to_mydata(task, order: Order, document):
    """Send ``document`` (a receipt or a credit) to AADE and say what became
    of it: the parsed row once it is registered, ``None`` otherwise.

    A transport failure retries with the SAME uid (AADE dedupes through
    error 228, whose MARK recovery ``submit_document`` already attempted),
    so repeats are safe; out of retries the status stays SUBMITTED for
    staff to resubmit. Terminal refusals are recorded on the document and
    as a staff note. Nothing here ever reaches the customer: a document
    AADE has not registered is not issued.
    """
    from order.accounting.mydata import (
        MyDataAuthError,
        MyDataDuplicateError,
        MyDataError,
        MyDataSendInFlight,
        MyDataTransportError,
        MyDataValidationError,
        submit_document,
    )
    from order.accounting.mydata.types import ERROR_PICKUP_POINT_UNKNOWN

    number = document.document_number
    refused_before = document.mydata_error_code
    message_before = document.mydata_error_message
    try:
        return submit_document(document)
    except MyDataSendInFlight as exc:
        # Another send of it may still be running: retries would all end
        # before its deadline, so none is spent; the sweep takes it up once
        # the deadline passed with no answer.
        logger.info(
            "myDATA submission for order #%s (%s) left to the sweep: %s",
            order.id,
            number,
            exc,
        )
        return None
    except MyDataTransportError as exc:
        logger.warning(
            "myDATA submission transport failure for order #%s (%s): %s",
            order.id,
            number,
            exc,
        )
        if task.request.retries < task.max_retries:
            raise task.retry(exc=exc) from exc
        return None
    except MyDataDuplicateError as exc:
        logger.error(
            "myDATA submission duplicate uid for order #%s (%s, uid=%s); "
            "MARK recovery via RequestTransmittedDocs failed. Manual "
            "reconciliation required: %s",
            order.id,
            number,
            document.mydata_uid,
            exc,
        )
        OrderHistory.log_note(
            order=order,
            note=(
                f"myDATA duplicate uid for {number}; automatic MARK "
                "recovery failed — manual reconciliation required "
                f"(uid={document.mydata_uid})"
            ),
        )
        return None
    except (MyDataValidationError, MyDataAuthError) as exc:
        logger.error(
            "myDATA submission terminal failure for order #%s (%s, "
            "code=%s): %s",
            order.id,
            number,
            exc.code,
            exc.message,
        )
        noted_before = (
            exc.code == refused_before == ERROR_PICKUP_POINT_UNKNOWN
        ) or (
            isinstance(exc, MyDataAuthError) and exc.message == message_before
        )
        if not noted_before:
            # A pickup point still unknown, or credentials still refused,
            # is noted once, not at every sweep that tries it again.
            OrderHistory.log_note(
                order=order,
                note=f"myDATA rejected {number}: {exc.code} {exc.message}",
            )
        return None
    except MyDataError as exc:
        # Catch-all for future subclasses we haven't branched on yet.
        logger.error(
            "myDATA submission unexpected error for order #%s (%s): %s",
            order.id,
            number,
            exc,
        )
        return None


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=5, default_retry_delay=120
)
def send_invoice_to_mydata(self, order_id: int) -> bool:
    """Submit the order's retail receipt to AADE myDATA.

    Chained from :func:`generate_order_invoice` once the PDF is rendered,
    and run by staff to resubmit a receipt that was refused or never
    answered. On success: persists MARK + ``qr_url`` on the receipt,
    regenerates the PDF (so the customer's copy carries the MARK + AADE's
    QR code), emails it, and releases the credit documents that were
    waiting for this MARK (:func:`issue_pending_credits`).
    """
    from order.accounting.invoicing import generate_invoice
    from order.models.invoice import Invoice

    try:
        order = Order.objects.select_related(
            "user", "country", "region", "pay_way"
        ).get(id=order_id)
    except Order.DoesNotExist:
        logger.error(
            "Could not submit receipt to myDATA - Order #%s not found",
            order_id,
        )
        return False

    invoice = getattr(order, "invoice", None)
    if invoice is not None and not invoice.has_document():
        # Its render never finished (a worker stopped after the row was
        # committed): rendered now, number and figures as issued, since
        # whatever asks for the send (the sweep, staff) may be the only
        # thing left that will.
        try:
            invoice = generate_invoice(order, force=True)
        except Exception:
            logger.exception(
                "Failed to render receipt PDF for order #%s", order_id
            )
    if invoice is None or not invoice.has_document():
        logger.warning(
            "myDATA submission skipped for order #%s — receipt PDF not ready",
            order_id,
        )
        return False

    response = _submit_document_to_mydata(self, order, invoice)
    if response is None:
        return False

    # Success: MARK + qr_url persisted. Re-render the PDF so the
    # version the customer downloads carries the authoritative AADE
    # QR (served from AADE's verification portal). ``force=True``
    # preserves ``invoice_number`` + ``issue_date`` (no counter gap).
    try:
        generate_invoice(order, force=True)
    except Exception:
        logger.exception(
            "Failed to re-render PDF with MARK for order #%s", order_id
        )

    OrderHistory.log_note(
        order=order,
        note=f"Receipt {invoice.invoice_number} registered with myDATA "
        f"(MARK={response.invoice_mark})",
    )
    # Read after the MARK is stored, never from the order loaded when this
    # task started: SHIPPED may have landed meanwhile, and its handler
    # (``handle_order_shipped``) found no MARK yet. Whichever of the two
    # comes second sends the email.
    registered = Invoice.objects.select_related("order").get(pk=invoice.pk)
    if not registered.awaits_the_parcel():
        # A receipt with movement reaches the customer when its parcel
        # leaves, so that an order canceled before then can have it
        # canceled in myDATA.
        dispatch_on_commit(send_invoice_email, [order_id])
    if registered.cancel_requested_at is not None:
        # Set aside for a cancel while it waited for its MARK (sent during
        # an outage, or corrected by staff after a refusal): it can be
        # canceled now.
        dispatch_on_commit(settle_canceled_receipt, [order_id])
    dispatch_on_commit(issue_pending_credits, [order_id])
    return True


def issue_receipt_for_pickup(order_id: int) -> None:
    """A carrier's voucher or parcel was recorded for the order: issue its
    receipt now, before the pickup, with movement indication
    (``order.accounting.movement``). Called on the order's first tracking
    number, the one place every carrier path records a live shipment
    (``issue_receipt_when_the_shipment_is_live``).

    Only where the platform issues the store's receipts by itself
    (``MyDataConfig.issues_automatically``); a store that issues by hand
    does it from the order, and a store that issues outside the platform
    sees nothing change. Shipping never waits for it: a myDATA that does
    not answer leaves the receipt issued under AADE's loss-of-connection
    procedure (``Invoice.transmission_failed_at``), transmitted again by
    ``retry_receipts_with_mydata``.
    """
    from order.accounting.mydata.config import load_config

    if load_config().issues_automatically():
        dispatch_on_commit(generate_order_invoice, [order_id])


@celery_app.task(base=MonitoredTask)
def retry_receipts_with_mydata() -> int:
    """Take up every receipt whose business with myDATA is unfinished: shipping
    never waits for it, so this sweep is how it gets done.

    - A receipt AADE may hold without having answered
      (``movement.sends_without_an_answer``: a send it did not answer and
      nothing decided since, or whose sender stopped mid-request), plain
      or with movement, held for a cancel or not, is sent again
      (``send_invoice_to_mydata``, its MARK looked up first), whose own
      retries an outage outlasts. One with movement goes plainly while its
      parcel is with the store, with ``transmissionFailure`` 3 once it left
      (``Invoice.transmission_failed_at``, «Απώλεια διασύνδεσης»). So is
      one set aside for a cancel that was never sent: the cancel needs its
      MARK.
    - A credit document (``RetailCredit``) AADE may hold without having
      answered, by the same rule, is sent again
      (``send_credit_to_mydata``).
    - A receipt set aside because its order was canceled before the parcel
      left (``Invoice.cancel_requested_at``) is settled
      (``settle_canceled_receipt``): its courier may have answered since, or
      myDATA may answer again.

    The state is the guarantee: rows are found by it once
    ``MYDATA_RETRY_AFTER_MINUTES`` passed since they were last touched. A
    send may still overlap a late retry of the task's own: both send the
    same uid, AADE registers one and answers the other with error 228, and
    ``service.submit_document`` never lets that answer, or anything after a
    MARK, demote the registered row. Two settles of one receipt never
    overlap (``settle_canceled_receipt`` holds a claim across its AADE
    call).
    """
    from order.accounting.movement import (
        sends_without_an_answer,
    )
    from order.accounting.mydata.config import load_config
    from order.accounting.mydata.types import ERROR_PICKUP_POINT_UNKNOWN
    from order.models import RetailCredit
    from order.models.invoice import Invoice, MyDataStatus

    if not load_config().is_ready():
        return 0
    cutoff = timezone.now() - timedelta(
        minutes=settings.MYDATA_RETRY_AFTER_MINUTES
    )
    batch = settings.MYDATA_RETRY_SWEEP_BATCH
    untouched = Invoice.objects.filter(updated_at__lte=cutoff)
    to_send = list(
        (
            sends_without_an_answer(untouched)
            # Set aside for a cancel before it was ever sent (its worker
            # stopped before the send): its registration is what lets the
            # cancel go through.
            | untouched.filter(
                cancel_requested_at__isnull=False,
                mydata_status=MyDataStatus.NOT_SENT,
            )
            # Refused before sending for a pickup point whose address the
            # carrier's catalogue lacked: its sync may have added it.
            | untouched.filter(
                mydata_status=MyDataStatus.REJECTED,
                mydata_error_code=ERROR_PICKUP_POINT_UNKNOWN,
            )
        )
        # Least recently touched first, so no batch of receipts that keep
        # failing keeps an older one out.
        .order_by("updated_at", "id")
        .values_list("order_id", flat=True)[:batch]
    )
    to_cancel = list(
        untouched.filter(cancel_requested_at__isnull=False)
        # A refused one waits for staff to correct it; its registration
        # queues the settle (``send_invoice_to_mydata``).
        .exclude(
            mydata_status__in=[MyDataStatus.REJECTED, MyDataStatus.CANCELED]
        )
        # One myDATA cannot see waits for staff (``_hold_for_staff``).
        .filter(cancel_held_for_staff_at__isnull=True)
        .exclude(order_id__in=to_send)
        .order_by("updated_at", "id")
        .values_list("order_id", flat=True)[:batch]
    )
    # One whose parcel left meanwhile goes with the loss-of-connection
    # indication: the claim of its send stamps it, under the row's lock
    # (``movement.stamp_if_left_unregistered``), before it is sent.
    # A broker that is down keeps each one as a DeferredDispatch, and the
    # sweep goes on to the next (``dispatch_on_commit``).
    for order_id in to_send:
        dispatch_on_commit(send_invoice_to_mydata, [order_id])
    # Touched, so a receipt still waiting (for the courier, for its MARK)
    # goes to the back and no batch of them keeps newer ones out.
    Invoice.objects.filter(order_id__in=to_cancel).update(
        updated_at=timezone.now()
    )
    for order_id in to_cancel:
        dispatch_on_commit(settle_canceled_receipt, [order_id])
    # Credit documents go through the same send and the same rule: one AADE
    # may hold without having answered is sent again, its MARK looked up
    # first (``send_credit_to_mydata``).
    credits_to_send = list(
        sends_without_an_answer(
            RetailCredit.objects.filter(updated_at__lte=cutoff)
        )
        .order_by("updated_at", "id")
        .values_list("pk", flat=True)[:batch]
    )
    for credit_id in credits_to_send:
        dispatch_on_commit(send_credit_to_mydata, [credit_id])
    if to_send or to_cancel or credits_to_send:
        logger.warning(
            "Queued %s receipt(s) and %s credit document(s) myDATA did not "
            "answer for, and %s receipt(s) of canceled orders",
            len(to_send),
            len(credits_to_send),
            len(to_cancel),
        )
    return len(to_send) + len(to_cancel) + len(credits_to_send)


def _settle_claim_key(order_id: int) -> str:
    return f"mydata:settle_canceled_receipt:{order_id}"


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=TRANSIENT_ERRORS,
    max_retries=5,
    retry_backoff=True,
    retry_jitter=True,
)
def settle_canceled_receipt(order_id: int) -> str:
    """Settle the receipt of an order canceled before its parcel left
    (``invoicing.hold_receipt_for_cancel``), once the courier answered the
    cancel (``metadata['cancellation']['shipment_cancel']``, the answer the
    cancel paths record):

    - the courier stopped the parcel, or there was none to stop: the sale
      and the movement never happened and the receipt was never handed to
      the customer, so it is canceled in myDATA (``CancelInvoice``) and no
      credit document follows;
    - the courier refused, or never answered: the parcel may be on its way,
      so the receipt stands, reaches the customer and goes the credit path
      (``issue_pending_credits``), as for any refund after a receipt.

    Only AADE refusing the cancel (``MyDataValidationError``) sends the
    receipt the credit path as well, with the reason in an order note;
    unless the refusal is a 301 and AADE does not list the receipt either:
    the store's credentials do not reach it, and it is held for staff
    (``_hold_for_staff``), with one note, out of the sweep.
    Credentials AADE refuses keep it set aside, with a note for staff; no
    answer from AADE (``MyDataTransportError``, a ``ConnectionError``) is
    retried here, then by ``retry_receipts_with_mydata``. Before any
    receipt goes the credit path AADE is asked whether it already holds a
    cancellation (a cancel whose answer was lost), so a receipt is never
    both canceled and credited. A receipt not registered yet waits for its
    MARK, whose arrival queues this again (``send_invoice_to_mydata``); one
    AADE refused waits for staff to correct and send it, and they are told.

    One settle per receipt at a time: the cancel's receiver, the courier's
    answer and the sweep may each queue it, and a claim
    (``cache.lock``, ``MYDATA_SETTLE_CLAIM_SECONDS``) is held from the read
    of the state, under the order's and the receipt's row locks, across
    the AADE calls, so a second run returns at once instead of sending
    ``CancelInvoice`` again and leaving AADE's "already canceled" error
    on the receipt. A claim that outlives a dead worker expires, and the
    run after it finds the cancellation AADE recorded
    (``find_cancellation_mark``). Shipping never waits on any of it.
    Returns what became of the receipt.
    """
    claim = cache.lock(
        _settle_claim_key(order_id),
        timeout=settings.MYDATA_SETTLE_CLAIM_SECONDS,
    )
    if not claim.acquire():
        return "settling_elsewhere"
    try:
        return _settle_canceled_receipt(order_id)
    finally:
        try:
            claim.release()
        except LockError:
            # The lease ran out and is another run's now; leaving it alone
            # is the point of the token.
            logger.warning(
                "Settling the receipt of order #%s outlasted its claim",
                order_id,
            )


def _settle_canceled_receipt(order_id: int) -> str:
    from order.accounting.mydata import (
        MyDataAuthError,
        MyDataNotListedError,
        MyDataValidationError,
        cancel_document,
    )
    from order.accounting.mydata.types import ERROR_MARK_NOT_FOUND
    from order.models.invoice import Invoice, MyDataStatus

    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        invoice = (
            Invoice.objects.select_for_update()
            .filter(order_id=order_id, cancel_requested_at__isnull=False)
            .exclude(mydata_status=MyDataStatus.CANCELED)
            .first()
        )
        if order is None or invoice is None:
            return "nothing_to_settle"
        if invoice.cancel_held_for_staff_at is not None:
            # Its order's note told staff; they settle it (``_hold_for_staff``).
            return "held_for_staff"
        answer = ((order.metadata or {}).get("cancellation") or {}).get(
            "shipment_cancel"
        )
        if answer is None:
            # A staff or system cancel asks the courier after its commit;
            # recording the answer queues this again
            # (``order_shipment_cancel_answered``).
            return "courier_not_answered"
        courier_stopped = "error" not in answer
        if courier_stopped and not invoice.mydata_mark:
            if invoice.mydata_status == MyDataStatus.REJECTED:
                OrderHistory.log_note(
                    order=order,
                    note=(
                        f"Receipt {invoice.invoice_number} of the canceled "
                        "order was refused by myDATA "
                        f"({invoice.mydata_error_code} "
                        f"{invoice.mydata_error_message}). Correct it and "
                        'press "Send receipt to myDATA": once it is '
                        "registered it is canceled in myDATA."
                    ),
                )
                return "awaiting_correction"
            # Still to be registered (its sends went unanswered): it is
            # canceled once it has its MARK.
            return "awaiting_mark"

    try:
        if not courier_stopped:
            return _credit_receipt_instead(
                order,
                invoice,
                f"the courier did not stop the parcel ({answer['error']})",
            )
        try:
            response = cancel_document(invoice)
        except MyDataValidationError as exc:
            logger.error(
                "myDATA refused to cancel receipt %s of canceled order #%s: %s",
                invoice.invoice_number,
                order_id,
                exc,
            )
            try:
                return _credit_receipt_instead(
                    order,
                    invoice,
                    f"myDATA refused to cancel it: {exc.code} {exc.message}",
                )
            except MyDataNotListedError:
                if exc.code != ERROR_MARK_NOT_FOUND:
                    raise
                return _hold_for_staff(order, invoice)
    except MyDataAuthError as exc:
        logger.error(
            "myDATA refused the store's credentials while settling receipt "
            "%s of canceled order #%s: %s",
            invoice.invoice_number,
            order_id,
            exc,
        )
        OrderHistory.log_note(
            order=order,
            note=(
                f"myDATA refused the store's credentials ({exc.message}): "
                f"receipt {invoice.invoice_number} of the canceled order "
                "stays set aside and is settled once they work."
            ),
        )
        return "credentials_refused"
    if response is None:
        return "myDATA_not_ready"
    OrderHistory.log_note(
        order=order,
        note=(
            f"Receipt {invoice.invoice_number} canceled in myDATA: the order "
            "was canceled before its parcel left (cancellation MARK="
            f"{response.cancellation_mark})"
        ),
    )
    return "canceled"


def _credit_receipt_instead(order: Order, invoice, reason: str) -> str:
    """The receipt of a canceled order stands after all: unless AADE holds
    a cancellation of it already (a cancel whose answer was lost), it is
    released to the credit path, handed to the customer, and staff are
    told why. Raises ``MyDataTransportError`` when AADE cannot be asked,
    so the receipt stays set aside for a retry."""
    from order.accounting.mydata.service import (
        find_cancellation_mark,
        record_cancellation,
    )

    if invoice.mydata_mark:
        cancellation_mark = find_cancellation_mark(invoice)
        if cancellation_mark is not None:
            record_cancellation(invoice, cancellation_mark=cancellation_mark)
            OrderHistory.log_note(
                order=order,
                note=(
                    f"Receipt {invoice.invoice_number} was already canceled "
                    "in myDATA (cancellation MARK="
                    f"{cancellation_mark}); no credit document is issued."
                ),
            )
            return "canceled"
    return _release_to_credit(order, invoice, reason)


def _release_to_credit(order: Order, invoice, reason: str) -> str:
    """Release the receipt of a canceled order from its hold: it stands,
    goes the credit path, reaches the customer, and an order note says
    why (*reason*)."""
    from order.accounting.movement import sends_without_an_answer
    from order.models.invoice import Invoice, MyDataStatus

    with transaction.atomic():
        # The MARK as it is now, under the row lock: a registration that
        # lands while this settles saw the hold and left the email to
        # whoever releases it; one that lands after this commits sees the
        # hold gone and sends the email itself.
        current = (
            Invoice.objects.select_for_update()
            .filter(pk=invoice.pk, cancel_requested_at__isnull=False)
            .exclude(mydata_status=MyDataStatus.CANCELED)
            .first()
        )
        if current is None:
            return "nothing_to_settle"
        current.cancel_requested_at = None
        current.save(update_fields=["cancel_requested_at", "updated_at"])
        OrderHistory.log_note(
            order=order,
            note=(
                f"Receipt {invoice.invoice_number} of the canceled order is "
                f"not canceled in myDATA: {reason}. A credit document is "
                "issued instead."
            ),
        )
        dispatch_on_commit(issue_pending_credits, [order.id])
        if current.mydata_mark:
            # It stands, so it reaches the customer (``awaits_the_parcel``).
            dispatch_on_commit(send_invoice_email, [order.id])
        elif (
            current.mydata_status == MyDataStatus.NOT_SENT
            or sends_without_an_answer(
                Invoice.objects.filter(pk=current.pk)
            ).exists()
        ):
            # Never sent (its worker stopped before the send), or AADE may
            # hold it without having answered: sent now, its MARK looked
            # up first, so its credits and email do not wait for the
            # sweep, which takes up one whose send is still in flight once
            # it is not. Its registration releases the credits and the
            # email.
            dispatch_on_commit(send_invoice_to_mydata, [order.id])
    return "credited"


def _hold_for_staff(order: Order, invoice) -> str:
    """Stop settling a receipt myDATA cannot see: it answered its cancel
    with 301 past ``MYDATA_CANCEL_NOT_FOUND_RETRY_MINUTES`` of the
    registration, and does not list it either (``find_cancellation_mark``),
    so the store's current credentials do not reach it. It may be
    registered, or already canceled, under others: a credit could count a
    tax document twice, and asking again changes nothing. It stays set
    aside (counted on the dashboard), out of the sweep, and one order note
    tells staff, who cancel it in myDATA or issue its credit documents
    (:func:`credit_receipt_held_for_staff`)."""
    from order.models.invoice import Invoice

    now = timezone.now()
    with transaction.atomic():
        held = Invoice.objects.filter(
            pk=invoice.pk,
            cancel_requested_at__isnull=False,
            cancel_held_for_staff_at__isnull=True,
        ).update(cancel_held_for_staff_at=now, updated_at=now)
        if held:
            OrderHistory.log_note(
                order=order,
                note=(
                    f"myDATA cannot see receipt {invoice.invoice_number} "
                    "with the store's current myDATA credentials: it "
                    "answers its cancel with 301 (not found) and does not "
                    "list it. Check with your accountant whether it was "
                    "registered or canceled under other credentials, then "
                    'press "Cancel receipt in myDATA (issued in error)" or '
                    '"Issue credit documents (myDATA)". It is not settled '
                    "automatically."
                ),
            )
    return "held_for_staff"


def credit_receipt_held_for_staff(order: Order) -> bool:
    """Staff's way to credit a receipt held for them
    (:func:`_hold_for_staff`): it stands, and goes the credit path like
    any other canceled order's receipt that was not canceled in myDATA.
    Returns ``False`` when the order has no receipt held for staff."""
    from order.models.invoice import Invoice, MyDataStatus

    invoice = (
        Invoice.objects.filter(
            order=order,
            cancel_requested_at__isnull=False,
            cancel_held_for_staff_at__isnull=False,
        )
        .exclude(mydata_status=MyDataStatus.CANCELED)
        .first()
    )
    if invoice is None:
        return False
    return (
        _release_to_credit(
            order,
            invoice,
            "myDATA could not see it with the store's credentials, and "
            "staff issued its credit documents",
        )
        == "credited"
    )


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def issue_pending_credits(self, order_id: int) -> list[int]:
    """Issue every retail credit document the order owes.

    Creates the missing ones (``order.accounting.credit_documents``: one per ledger
    refund, one settlement for an order closed without a sale) and submits
    each credit that is not yet registered (one a run died before
    submitting, or AADE refused, is simply sent again). Dispatched by every
    trigger (a refund, an order closing, the receipt being registered,
    staff) and idempotent: with nothing owed and everything registered it
    does nothing.
    """
    from order.accounting.credit_documents import create_pending_credits
    from order.models import RetailCredit
    from order.models.invoice import MyDataStatus

    create_pending_credits(order_id)
    pending = list(
        RetailCredit.objects.filter(order_id=order_id, mydata_mark__isnull=True)
        .exclude(mydata_status=MyDataStatus.CANCELED)
        .order_by("id")
        .values_list("pk", flat=True)
    )
    for credit_id in pending:
        dispatch_on_commit(send_credit_to_mydata, [credit_id])
    return pending


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=5, default_retry_delay=120
)
def send_credit_to_mydata(self, credit_id: int) -> bool:
    """Submit one retail credit document (11.4) to AADE myDATA.

    Linked to the MARK of the receipt it credits. On success the PDF is
    rendered and emailed. Staff resubmit a refused one with this task.
    """
    from order.models import RetailCredit

    credit = (
        RetailCredit.objects.select_related(
            "order", "order__user", "order__country", "invoice"
        )
        .filter(pk=credit_id)
        .first()
    )
    if credit is None:
        logger.error(
            "Could not submit credit to myDATA - credit #%s not found",
            credit_id,
        )
        return False
    if credit.mydata_mark:
        return True
    if not credit.invoice.mydata_mark:
        logger.warning(
            "Credit #%s waits for the MARK of receipt %s",
            credit.pk,
            credit.invoice.invoice_number,
        )
        return False

    order = credit.order
    response = _submit_document_to_mydata(self, order, credit)
    if response is None:
        return False

    OrderHistory.log_note(
        order=order,
        note=f"Credit receipt {credit.credit_number} registered with "
        f"myDATA (MARK={response.invoice_mark}, credits receipt "
        f"{credit.invoice.invoice_number})",
    )
    dispatch_on_commit(send_credit_email, [credit_id])
    return True


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_credit_email(self, credit_id: int) -> bool:
    """Render a registered credit document's PDF and email it to the buyer.

    Idempotent per credit through a flag in ``Order.metadata``, released on
    permanent failure so staff can resend.
    """
    from order.accounting.invoicing import render_credit_pdf
    from order.models import RetailCredit

    credit = (
        RetailCredit.objects.select_related(
            "order", "order__user", "order__country", "invoice"
        )
        .filter(pk=credit_id)
        .first()
    )
    if credit is None:
        logger.error(
            "Could not send credit email - credit #%s not found", credit_id
        )
        return False
    order = credit.order
    flag = _credit_email_flag(credit_id)
    email_sent = False
    try:
        if (self.request.retries == 0) and (
            not _reserve_document_email(order.id, flag)
        ):
            return True
        # Rendered here, after the MARK landed, so the PDF carries it and
        # AADE's QR; a retry re-renders from the same frozen figures.
        render_credit_pdf(credit)
        if not _send_document_email(credit, is_credit=True):
            logger.info(
                "Credit email for credit #%s not sent: turned off", credit_id
            )
            return True
        email_sent = True
        OrderHistory.log_note(
            order=order,
            note=f"Credit receipt {credit.credit_number} emailed to "
            f"{order.email}",
        )
        return True
    except Exception as e:
        logger.exception(
            "Error sending credit email for credit #%s",
            credit_id,
            extra={"credit_id": credit_id, "error": str(e)},
        )
        if email_sent:
            return True
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        _release_document_email(order.id, flag)
        return False


def _cancel_registered_document(task, order: Order, document) -> bool:
    """Send ``CancelInvoice`` for a registered document issued in error."""
    from order.accounting.mydata import (
        MyDataError,
        MyDataTransportError,
    )
    from order.accounting.mydata import (
        cancel_document as _cancel,
    )

    if not document.mydata_mark:
        logger.warning(
            "myDATA cancellation skipped for order #%s — %s has no MARK",
            order.id,
            document.document_number,
        )
        return False

    try:
        response = _cancel(document)
    except MyDataTransportError as exc:
        if task.request.retries < task.max_retries:
            raise task.retry(exc=exc) from exc
        logger.error(
            "myDATA cancellation exhausted retries for order #%s: %s",
            order.id,
            exc,
        )
        return False
    except MyDataError as exc:
        logger.error(
            "myDATA cancellation terminal failure for order #%s: %s",
            order.id,
            exc,
        )
        return False

    if response is None:
        # Integration disabled — treat as no-op success so the admin
        # action doesn't loop on retries.
        return True

    OrderHistory.log_note(
        order=order,
        note=f"{document.document_number} cancelled in myDATA "
        f"(cancellation MARK={response.cancellation_mark})",
    )
    return True


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def cancel_mydata_invoice(self, order_id: int) -> bool:
    """Send a ``CancelInvoice`` for the order's registered receipt.

    Only for a receipt issued in error, and only from the admin action:
    a sale that did not happen is undone by a credit document, never by
    cancelling the receipt. Fails fast when there is no MARK.
    """
    try:
        order = Order.objects.select_related("user").get(id=order_id)
    except Order.DoesNotExist:
        logger.error(
            "Could not cancel myDATA receipt - Order #%s not found", order_id
        )
        return False
    invoice = getattr(order, "invoice", None)
    if invoice is None:
        return False
    return _cancel_registered_document(self, order, invoice)


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def cancel_mydata_credit(self, credit_id: int) -> bool:
    """Send a ``CancelInvoice`` for a credit document issued in error
    (admin only)."""
    from order.models import RetailCredit

    credit = (
        RetailCredit.objects.select_related("order")
        .filter(pk=credit_id)
        .first()
    )
    if credit is None:
        return False
    return _cancel_registered_document(self, credit.order, credit)


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=TRANSIENT_ERRORS,
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def cleanup_expired_stock_reservations() -> int:
    """
    Cleanup expired stock reservations.

    This task should be run periodically (every 5 minutes) to release
    expired stock reservations and make the stock available again for
    other customers.

    Expired reservations are those where:
    - expires_at < current_time (past STOCK_RESERVATION_TTL_MINUTES)
    - consumed = False (not yet converted to sale or released)

    Returns:
        int: Number of expired reservations that were cleaned up
    """
    try:
        from order.inventory.stock import StockManager

        count = StockManager.cleanup_expired_reservations()

        if count > 0:
            logger.info(
                f"Cleaned up {count} expired stock reservations",
                extra={"cleaned_count": count},
            )

        return count

    except Exception as e:
        logger.exception(
            "Error cleaning up expired stock reservations",
            extra={"error": str(e)},
        )
        return 0


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=TRANSIENT_ERRORS,
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def complete_paid_delivered_orders() -> dict[str, int]:
    """Advance every DELIVERED order that is paid to COMPLETED.

    ``OrderService.maybe_advance_to_completed`` runs inline at exactly
    two moments — the carrier reporting DELIVERED and the COD reconcile
    flipping the payment — and nowhere else. Anything that pays a
    delivered order outside those two (staff entering a bank transfer or
    cash by hand in the admin, a payment webhook landing after the
    parcel) leaves it at DELIVERED for good, and so does an inline call
    that failed once. Prod 2026-09-20: orders 252 and 253 had their COD
    payout matched on 09-07 and were still DELIVERED; 73 and 92 were
    paid by hand and never moved.

    Idempotent and cheap (two indexed equality filters). Silent for the
    customer, like every automatic COMPLETED: the shopper already has the
    DELIVERED message and this is bookkeeping.

    The row is loaded whole, not through ``values``: ``maybe_advance_to_
    completed`` re-reads what it needs, and ``Order.__init__`` snapshots
    columns a partial load would leave deferred.
    """
    candidate_ids = list(
        Order.objects.filter(
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
        ).values_list("id", flat=True)
    )
    advanced = 0
    for order_id in candidate_ids:
        try:
            with transaction.atomic():
                order = Order.objects.get(pk=order_id)
                after = OrderService.maybe_advance_to_completed(
                    order, silent_for_customer=True
                )
        except Exception:
            logger.exception(
                "complete_paid_delivered_orders: order %s could not be "
                "advanced",
                order_id,
            )
            continue
        if after.status == OrderStatus.COMPLETED:
            advanced += 1

    if candidate_ids:
        logger.info(
            "complete_paid_delivered_orders: candidates=%s advanced=%s",
            len(candidate_ids),
            advanced,
        )
    return {"candidates": len(candidate_ids), "advanced": advanced}


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=TRANSIENT_ERRORS,
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def reconcile_pending_refunds() -> dict[str, int]:
    """Ask the payment provider what became of every refund attempt still
    PENDING after ``REFUND_RECONCILE_AFTER_MINUTES``.

    A provider call that ended without an answer leaves its attempt PENDING,
    holding its money until something resolves it
    (``order.refunds.refund_reconciliation``). A refund found at the provider is
    recorded, one the provider reports failed is declined, and one the
    lookup cannot decide (nothing found, several candidates, the provider
    unreadable) stays PENDING for staff: the dashboard counts it. Safe to
    repeat: an attempt is only ever resolved once, under the order lock.
    """
    counts = refund_reconciliation.reconcile_overdue()
    if any(counts.values()):
        logger.info("reconcile_pending_refunds: %s", counts)
    return counts


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=TRANSIENT_ERRORS,
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def allocate_credit_sources() -> int:
    """Decide every refund, refund attempt and returned chargeback that owes
    a credit and has no allocation (``order.accounting.credit_allocation_backfill``),
    and ask again for the receipts that were waiting on them.

    The write paths decide an order's unallocated sources before they read
    or take room (``credit_allocation.allocate_order``); this decides what
    none of them has touched since: what a release that did not know about
    allocations booked, and a source that waited for a refund claim still
    PENDING to resolve (settled, declined, or recorded by staff, whichever
    path resolved it). When it decided sources of an order that has a
    receipt, the credit documents are requested. An order flagged
    ``receipt_deferred`` that has no undecided source left is issued its
    receipt now (``generate_order_invoice``). Idempotent; each order is
    handled under its lock. Returns how many sources it decided.
    """
    from order.accounting import credit_allocation
    from order.accounting.credit_allocation_backfill import (
        backfill_credit_allocations,
    )

    done = backfill_credit_allocations()
    if done:
        logger.info("allocate_credit_sources: allocated %s sources", done)
    for order in Order.objects.filter(
        **{f"metadata__{RECEIPT_DEFERRED_FLAG}": True}
    ):
        if hasattr(order, "invoice"):
            set_receipt_deferred(order.pk, False)
        elif not credit_allocation.undecided(order):
            # The flag means "waiting for undecided sources", nothing more:
            # they are decided, so from here the receipt follows the normal
            # failure path (a failure record, retries) like any other.
            set_receipt_deferred(order.pk, False)
            dispatch_on_commit(generate_order_invoice, [order.pk])
    return done


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=TRANSIENT_ERRORS,
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def auto_cancel_stuck_pending_orders() -> dict[str, int]:
    """Auto-cancel PENDING orders that will never be paid.

    Two buckets:
    - Orders with FAILED payment older than
      ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES (default 30). A failed
      attempt leaves a Viva payment order payable until its expiry and a
      Stripe PaymentIntent open for another card, so the provider is asked
      to close the payment first, like the next bucket.
    - Orders with PENDING payment whose hold has ended: the payment link
      or session stopped being payable (``Order.payment_hold_expires_at``,
      ORDER_ONLINE_PAYMENT_HOLD_MINUTES) plus
      ORDER_ONLINE_PAYMENT_GRACE_MINUTES for a confirmation still on its
      way. The stock is released only when the payment can no longer
      succeed, so a late payment cannot book money for goods nobody
      reserved.

    Before cancelling either bucket, the provider is asked to close a
    payment that has no expiry of its own (a Stripe PaymentIntent). One the
    provider says is past the point of no return (processing, succeeded)
    leaves the order open: its webhook settles it.

    Delegates to OrderService.cancel_order which restores stock via
    StockManager.increment_stock and releases any outstanding
    StockReservation records.
    """
    now = timezone.now()

    # Every order is selected through ``payment_hold.swept_orders``, the
    # definition ``paymentDecidedBy`` is computed from, so the two cannot
    # drift. It leaves out an order a provider confirmed a charge for
    # that did not match its total (usually a shopper paying on a stale
    # checkout tab after the total moved): cancelling it here would close
    # a CHARGED order with refund_payment=False and email the customer
    # that it was cancelled. It needs a human.
    swept = payment_hold.swept_orders().values("pk")
    failed_qs = (
        payment_hold.expired_failed_payment_orders(now)
        .filter(pk__in=swept)
        .select_related("pay_way")
    )
    # Only auto-cancel online-payment orders — offline methods (COD,
    # bank transfer) are expected to sit PENDING until the customer
    # pays, sometimes for days (measured ACS lag ~4 days), so cancelling
    # them would kill live orders. ``expired_unpaid_online_orders``
    # selects the online ones; an order with no pay_way at all (NULL) is
    # treated as offline-equivalent.
    pending_qs = (
        payment_hold.expired_unpaid_online_orders(now)
        .filter(pk__in=swept)
        .select_related("pay_way")
    )

    review_ids = list(
        payment_hold.payment_review_orders().values_list("id", flat=True)
    )
    if review_ids:
        logger.warning(
            "Skipping auto-cancel for %s order(s) with a confirmed charge "
            "that did not match the order total: %s",
            len(review_ids),
            review_ids,
        )

    canceled_failed = 0
    canceled_pending = 0
    skipped_open = 0
    errors = 0

    def _payment_closed(order):
        # Outside the row lock: this is a provider round trip. An order
        # with no pay way (SET_NULL when it is deleted) or a pay way with
        # no registered provider has no online payment to close. A
        # provider that cannot be built (credentials removed) cannot close
        # the payment either, but the payment may still land (a Viva
        # payment order stays payable until it expires), so it waits for
        # the stored hold like any payment that could not be closed.
        if order.pay_way is None:
            return True
        provider_class = get_payment_provider_class(order.pay_way.provider_code)
        if provider_class is None:
            return True
        try:
            provider = provider_class()
        except ImproperlyConfigured:
            closed = payment_hold.hold_ended(order)
        else:
            closed = provider.close_payment(order)
        if not closed:
            logger.warning(
                "Auto-cancel skipped order %s: its payment may still land "
                "(the provider is processing it, or its hold is running)",
                order.id,
            )
        return closed

    def _cancel_with_lock(order, reason):
        # Re-fetch under row lock so concurrent beat workers can't both
        # cancel the same order. skip_locked means the losing worker
        # bails out cleanly instead of blocking.
        with transaction.atomic():
            locked = (
                Order.objects.select_for_update(skip_locked=True)
                .filter(pk=order.pk, status=OrderStatus.PENDING)
                .first()
            )
            if locked is None:
                return False
            OrderService.cancel_order(
                locked,
                reason=reason,
                refund_payment=False,
                customer_initiated=False,
            )
            return True

    for order in failed_qs.iterator():
        try:
            # A failed attempt leaves a Stripe PaymentIntent open for
            # another card: it is closed like a stale one, or the shopper
            # could still pay an order whose stock was released.
            if not _payment_closed(order):
                skipped_open += 1
                continue
            if _cancel_with_lock(
                order,
                "Auto-canceled: payment failed and not retried",
            ):
                canceled_failed += 1
        except Exception:
            errors += 1
            logger.exception(
                "Auto-cancel (failed-payment) error for order %s",
                order.id,
            )

    for order in pending_qs.iterator():
        try:
            if not _payment_closed(order):
                skipped_open += 1
                continue
            if _cancel_with_lock(
                order,
                "Auto-canceled: payment never completed",
            ):
                canceled_pending += 1
        except Exception:
            errors += 1
            logger.exception(
                "Auto-cancel (stale-pending) error for order %s",
                order.id,
            )

    total = canceled_failed + canceled_pending
    if total or errors:
        logger.info(
            "auto_cancel_stuck_pending_orders: canceled_failed=%s canceled_pending=%s errors=%s",
            canceled_failed,
            canceled_pending,
            errors,
        )

    # What is still held after the sweep: a pile-up of orders nobody is
    # paying for locks stock other shoppers cannot buy.
    alert_threshold = int(store_setting(payment_hold.ALERT_SETTING))
    if alert_threshold > 0:
        unpaid = Order.objects.holding_stock_for_online_payment().count()
        if unpaid >= alert_threshold:
            logger.warning(
                payment_hold.SPIKE_MESSAGE,
                extra={
                    "fields": {
                        "unpaid_online_orders": unpaid,
                        "threshold": alert_threshold,
                    }
                },
            )
    return {
        "canceled_failed": canceled_failed,
        "canceled_pending": canceled_pending,
        "skipped_open": skipped_open,
        "errors": errors,
    }


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=TRANSIENT_ERRORS,
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def finish_pending_shipment_cancels(
    *, max_per_run: int = 100
) -> dict[str, int]:
    """Ask the courier to cancel the shipment of every canceled order
    that never got that far (#713).

    A staff or system cancel commits first and asks the courier
    afterwards, with no row locked (``OrderService.cancel_order``). A
    worker that stops between the two leaves the order CANCELED, and
    refunded, with a voucher the courier still holds. The cancel marks
    the order in its own transaction
    (``SHIPMENT_CANCEL_PENDING_FLAG``), so that state is found here and
    finished by the same ``cancel_attached_shipment``, which records the
    courier's answer and removes the flag.

    Only a cancel older than
    ``ORDER_SHIPMENT_CANCEL_SWEEP_AFTER_SECONDS`` is taken: a younger one
    may still be waiting for its courier. Safe to run beside it all the
    same, and beside another run of itself, so it takes no lock of its
    own: a cancel the other one has in flight refuses this one as
    already running (``CarrierBusyError``), which is not an answer and
    leaves the flag; both carriers' cancel is a no-op on a shipment
    already canceled; and the first answer recorded is the one kept.
    The same holds for a creation in flight or still settling: the order
    stays flagged and is asked about on a later run.

    It also asks again for a courier that gave no answer
    (``CarrierUnavailableError``), on every run until
    ``ORDER_SHIPMENT_CANCEL_RETRY_HOURS`` after the cancel, when
    ``cancel_attached_shipment`` records the last failure for staff.
    A courier that gives no answer in a run is not asked about its other
    orders in that run: during an outage each would wait out the same
    timeout and keep the run, and the worker, busy for nothing. An order
    whose retries are over is still asked once, so it gets its answer;
    "over" is read when the order comes up, since the courier calls
    before it take time.

    *max_per_run* bounds the couriers asked in one run, not the orders
    looked at: the orders a silent courier holds are passed over without a
    call, so however many it holds they never keep another courier's
    pending cancels from being asked. The candidates are read as ids and
    times only, oldest first, and each order asked is loaded then, fresh.

    Beat-scheduled through
    ``tenant.tasks.fanout_finish_pending_shipment_cancels``.
    """
    cutoff = timezone.now() - timedelta(
        seconds=settings.ORDER_SHIPMENT_CANCEL_SWEEP_AFTER_SECONDS
    )
    pending = (
        Order.objects.filter(
            status=OrderStatus.CANCELED,
            metadata__has_key=SHIPMENT_CANCEL_PENDING_FLAG,
            status_updated_at__lt=cutoff,
        )
        .order_by("status_updated_at")
        .values_list("pk", "shipping_provider_id", "status_updated_at")
    )

    finished = still_pending = asked = 0
    silent_couriers: set[int] = set()
    for pk, provider_id, canceled_at in pending:
        if asked >= max_per_run or (
            provider_id in silent_couriers
            and timezone.now() < shipment_cancel_retry_until(canceled_at)
        ):
            still_pending += 1
            continue
        order = Order.objects.filter(
            pk=pk, metadata__has_key=SHIPMENT_CANCEL_PENDING_FLAG
        ).first()
        if order is None:
            # Answered meanwhile, by the first attempt or another run.
            continue
        asked += 1
        logger.warning(
            "Order %s was canceled at %s and holds no answer from its "
            "courier to the shipment cancel: asking now",
            order.id,
            order.status_updated_at,
        )
        cancellation = (order.metadata or {}).get("cancellation") or {}
        unanswered = OrderService.cancel_attached_shipment(
            order, cancellation.get("reason", "")
        )
        if unanswered and order.shipping_provider_id is not None:
            silent_couriers.add(order.shipping_provider_id)
        # ``order`` carries the metadata as written: the flag is gone
        # once an answer is recorded, and stays for a shipment that is
        # being worked on elsewhere.
        if SHIPMENT_CANCEL_PENDING_FLAG in (order.metadata or {}):
            still_pending += 1
        else:
            finished += 1
    return {"finished": finished, "still_pending": still_pending}


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def send_checkout_abandonment_emails() -> int:
    """Email customers who reserved stock but never placed an order.

    Finds authenticated users whose StockReservations expired without
    being consumed (no order created) and who have not yet been
    notified. Uses StockReservation.abandonment_notified as the
    idempotency flag — flipped to True for the whole session after a
    successful send, preventing repeat emails across daily runs.
    """
    from cart.models.cart import Cart
    from order.models.stock_reservation import StockReservation

    hours = store_setting("CHECKOUT_ABANDONMENT_HOURS")
    cutoff = timezone.now() - timedelta(hours=hours)

    # NOTE: we intentionally do NOT filter on `consumed=False`. The
    # `cleanup_expired_stock_reservations` task runs every 5 minutes
    # and flips `consumed=True` on every expired reservation, so
    # by the time this task fires (daily) the flag is always True.
    # `abandonment_notified=False` is the correct idempotency gate,
    # and `order__isnull=True` still restricts us to reservations
    # that never converted into a real sale.
    abandoned_session_ids = list(
        StockReservation.objects.filter(
            abandonment_notified=False,
            expires_at__lt=timezone.now(),
            updated_at__lt=cutoff,
            order__isnull=True,
        )
        .values_list("session_id", flat=True)
        .distinct()
    )

    if not abandoned_session_ids:
        return 0

    carts = Cart.objects.select_related("user").filter(
        uuid__in=abandoned_session_ids,
        user__isnull=False,
    )

    sent = 0
    for cart in carts:
        try:
            deliveries = send_email("cart.checkout_abandoned", cart)
            # Settled whether it was sent or not: an account that opted
            # out through the one-click header, a store that switched the
            # reminder off, or a closed cart feature must not have today's
            # reservations found again tomorrow, nor a months-old reminder
            # go out the day the reminder is back on.
            StockReservation.objects.filter(session_id=str(cart.uuid)).update(
                abandonment_notified=True
            )
            sent += was_sent(deliveries)
        except Exception:
            logger.exception(
                "Error sending checkout-abandonment email for cart %s",
                cart.id,
            )

    if sent:
        logger.info("Sent %s checkout-abandonment emails", sent)
    return sent


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def send_review_request_emails() -> dict[str, int]:
    """Remind customers to review an order, once, a few days after delivery.

    Which orders is
    ``order.communications.review_request.awaiting_review_request`` (the
    store's ``REVIEW_REQUEST_DELAY_DAYS``; 0 is off). Each order is CLAIMED
    first, by stamping ``Order.review_request_sent_at`` where it is still
    empty, so two overlapping runs cannot both mail it. The claim is kept
    when the order is settled without a mail (nothing left to review, the
    customer opted out, the store switched the email off) and when the
    failure cannot succeed on another attempt; it is released after a
    transient failure, and the next daily run is the retry for as long as
    the order stays in the catch-up window.
    """
    window = delivery_window()
    if window is None:
        return {"sent": 0, "skipped": 0, "failed": 0}
    sent = skipped = failed = 0
    for order in awaiting_review_request(window).select_related("user"):
        claimed = Order.objects.filter(
            pk=order.pk, review_request_sent_at__isnull=True
        ).update(review_request_sent_at=timezone.now())
        if not claimed:
            continue
        try:
            if not unreviewed_products(order):
                skipped += 1
                continue
            if was_sent(send_email("order.review_request", order)):
                sent += 1
            else:
                skipped += 1
        except Exception as error:
            failed += 1
            logger.exception("Review request for order #%s failed", order.id)
            if not is_permanent_failure(error):
                Order.objects.filter(pk=order.pk).update(
                    review_request_sent_at=None
                )
    if sent or failed:
        logger.info(
            "Review requests: sent=%s skipped=%s failed=%s",
            sent,
            skipped,
            failed,
        )
    return {"sent": sent, "skipped": skipped, "failed": failed}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def purge_expired_pending_payment_fees() -> dict[str, int]:
    """Delete fees older than ``PENDING_PAYMENT_FEE_RETENTION_DAYS`` whose
    payment was never booked.

    A fee is deleted as soon as its payment is recorded, so only the fee of
    a payment that never settled reaches this sweep. Runs per tenant through
    ``tenant.tasks.fanout_purge_expired_pending_payment_fees``.
    """
    cutoff = timezone.now() - timedelta(
        days=settings.PENDING_PAYMENT_FEE_RETENTION_DAYS
    )
    deleted = delete_in_batches(
        PendingPaymentFee.objects.filter(created_at__lt=cutoff)
    )
    if deleted:
        logger.info("Purged %s pending payment fees", deleted)
    return {"deleted": deleted}
