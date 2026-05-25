import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _
from django.utils import translation

from extra_settings.models import Setting

from core import celery_app
from core.tasks import MonitoredTask
from core.utils.i18n import get_order_language, get_user_language
from core.utils.tenant_urls import get_tenant_base_url, get_tenant_frontend_url
from tenant.credentials import tenant_contact_email, tenant_from_email
from order.enum.status import OrderStatus, PaymentStatus
from order.models import Order, OrderHistory
from order.services import OrderService
from user.utils.subscription import build_transactional_list_headers

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

# Legacy flag name — kept so existing metadata rows are still readable
# by _release_confirmation_email (used in existing tests).
CONFIRMATION_EMAIL_SENT_FLAG = "confirmation_email_sent"


def _confirmation_lock_key(order_id: int) -> str:
    return f"{CONFIRMATION_EMAIL_LOCK_PREFIX}{order_id}"


def _confirmation_already_sent(metadata: dict | None) -> bool:
    """Return True if the permanent DB timestamp shows the email was sent.

    The boolean ``CONFIRMATION_EMAIL_SENT_FLAG`` is checked as a
    fallback because pre-timestamp-key orders persisted in the DB
    only have that key set. New writes use the timestamp only.
    """
    meta = metadata or {}
    return bool(
        meta.get(CONFIRMATION_EMAIL_SENT_AT_KEY)
        or meta.get(CONFIRMATION_EMAIL_SENT_FLAG)
    )


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


def _release_confirmation_email(order_id: int) -> None:
    """Clear the confirmation-email permanent flags so an admin or test
    can trigger a resend. Also releases any lingering Redis lock."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=order_id).first()
        if order is None or not order.metadata:
            return
        changed = False
        for key in (
            CONFIRMATION_EMAIL_SENT_AT_KEY,
            CONFIRMATION_EMAIL_SENT_FLAG,
        ):
            if order.metadata.pop(key, None) is not None:
                changed = True
        if changed:
            order.save(update_fields=["metadata"])
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
            .prefetch_related(
                "items__product__translations", "pay_way__translations"
            )
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

        pay_way = order.pay_way
        is_paid = bool(
            pay_way
            and pay_way.is_online_payment
            and order.payment_status == PaymentStatus.COMPLETED
        )

        with translation.override(get_order_language(order)):
            if is_paid:
                template_base = "emails/order/order_payment_confirmed"
                subject = _("Payment Confirmed - Order #{order_id}").format(
                    order_id=order.id
                )
            else:
                template_base = "emails/order/order_received"
                subject = _("Order Received - #{order_id}").format(
                    order_id=order.id
                )

            payment_instructions = ""
            if pay_way and not pay_way.is_online_payment:
                payment_instructions = (
                    pay_way.safe_translation_getter(
                        "instructions", any_language=True
                    )
                    or ""
                )

            context = {
                "order": order,
                "items": order.items.all(),
                "pay_way": pay_way,
                "payment_instructions": payment_instructions,
                "is_paid": is_paid,
                "SITE_NAME": settings.SITE_NAME,
                "INFO_EMAIL": tenant_contact_email(),
                "SITE_URL": get_tenant_base_url(),
                "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            }

            text_content = render_to_string(f"{template_base}.txt", context)
            html_content = render_to_string(f"{template_base}.html", context)

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            tenant_from_email(),
            [order.email],
            reply_to=[tenant_contact_email()],
            headers=build_transactional_list_headers(
                list_id="order_confirmation"
            ),
        )
        msg.attach_alternative(html_content, "text/html")

        msg.send()

        # Write permanent flag + release lock atomically (DB commit, then
        # cache.delete). From this point on every future call to this task
        # will short-circuit on the DB flag.
        _mark_confirmation_sent(order_id)

        logger.info(
            "Order confirmation email sent for order #%s",
            order.id,
            extra={"order_id": order.id, "email": order.email},
        )

        OrderHistory.log_note(
            order=order,
            note=f"Order confirmation email sent to {order.email}",
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


STATUS_UPDATE_EMAIL_SENT_FLAG = "status_update_email_sent"
# TTL to hold the reservation after a successful send, so retry cycles
# triggered by transient ack failures don't re-send to the customer.
FINAL_RESERVATION_TTL = 86_400  # 24 hours


def _status_update_reservation_key(order_id: int, new_status: str) -> str:
    """Build the metadata key for a specific order+status combination.

    Different status transitions for the same order are independent —
    the PROCESSING email must not block the SHIPPED email.
    """
    return f"{STATUS_UPDATE_EMAIL_SENT_FLAG}_{new_status}"


def _reserve_status_update_email(order_id: int, new_status: str) -> bool:
    """Atomically claim the status-update-email slot for (order, status).

    Returns True if this caller won the race and should proceed with the
    send, False if another caller has already sent (or is sending) the
    email. The reservation includes BOTH order_id AND new_status so
    different status transitions remain independent.
    Raises Order.DoesNotExist if the order is missing.
    """
    flag_key = _status_update_reservation_key(order_id, new_status)
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
    flag_key = _status_update_reservation_key(order_id, new_status)
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
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def send_dispute_notification_email(
    self, order_id: int, dispute_id: str
) -> bool:
    """Notify staff that a Stripe dispute has been opened for an order.

    Sends to the staff group email configured via ``INFO_EMAIL``.  Does NOT
    change order status — that is a manual decision.  Includes structured
    log fields so ops can correlate the Celery log line with the Stripe
    dashboard entry.
    """
    try:
        order = Order.objects.select_related("user", "pay_way").get(id=order_id)

        reason = (order.metadata or {}).get("dispute_reason", "unknown")

        context = {
            "order": order,
            "dispute_id": dispute_id,
            "reason": reason,
            "SITE_NAME": settings.SITE_NAME,
            "INFO_EMAIL": tenant_contact_email(),
            "SITE_URL": get_tenant_base_url(),
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

        subject = (
            f"[{settings.SITE_NAME}] Stripe dispute opened — Order #{order_id}"
        )

        try:
            text_content = render_to_string(
                "emails/order/dispute_notification.txt", context
            )
            html_content = render_to_string(
                "emails/order/dispute_notification.html", context
            )
        except Exception:
            # Fallback plain-text if template not yet authored
            text_content = (
                f"A Stripe dispute has been opened for Order #{order_id}.\n"
                f"Dispute ID: {dispute_id}\n"
                f"Reason: {reason}\n\n"
                f"Please review this dispute in the Stripe dashboard and take action."
            )
            html_content = (
                f"<p>A Stripe dispute has been opened for "
                f"<strong>Order #{order_id}</strong>.</p>"
                f"<ul>"
                f"<li>Dispute ID: {dispute_id}</li>"
                f"<li>Reason: {reason}</li>"
                f"</ul>"
                f"<p>Please review this dispute in the Stripe dashboard.</p>"
            )

        staff_email = tenant_contact_email()
        if not staff_email:
            logger.warning(
                "send_dispute_notification_email: no contact email configured — skipping",
                extra={"order_id": order_id, "dispute_id": dispute_id},
            )
            return False

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            tenant_from_email(),
            [staff_email],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()

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
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def send_admin_new_order_email(self, order_id: int) -> bool:
    """Notify the store operator that a new order has been placed.

    This is a MERCHANT-facing alert, not a platform one: it goes to the
    tenant's ``tenant_contact_email()`` FROM ``tenant_from_email()``, so
    each store's operator gets their own orders (NOT ``settings.ADMINS``,
    which is the SaaS platform's admin list shared across all tenants).
    Skips silently if the tenant has no contact email configured.
    """
    try:
        order = (
            Order.objects.select_related("user", "country", "region", "pay_way")
            .prefetch_related(
                "items__product__translations", "pay_way__translations"
            )
            .get(id=order_id)
        )

        staff_email = tenant_contact_email()
        if not staff_email:
            logger.warning(
                "send_admin_new_order_email: no contact email configured — skipping",
                extra={"order_id": order_id},
            )
            return False

        context = {
            "order": order,
            "items": order.items.all(),
            "pay_way": order.pay_way,
            "SITE_NAME": settings.SITE_NAME,
            "INFO_EMAIL": staff_email,
            "SITE_URL": get_tenant_base_url(),
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

        text_content = render_to_string(
            "emails/order/admin_new_order.txt", context
        )
        html_content = render_to_string(
            "emails/order/admin_new_order.html", context
        )

        msg = EmailMultiAlternatives(
            f"New order — #{order_id}",
            text_content,
            tenant_from_email(),
            [staff_email],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()

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

    Mirrors `_reserve_confirmation_email` so concurrent webhook
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
    reserved_this_call = False
    try:
        if self.request.retries == 0:
            if not _reserve_payment_failed_email(order_id):
                logger.info(
                    "Payment failed email already sent for order #%s, skipping",
                    order_id,
                )
                return True
            reserved_this_call = True

        order = (
            Order.objects.select_related("user", "pay_way")
            .prefetch_related("items__product__translations")
            .get(id=order_id)
        )

        retry_url = get_tenant_frontend_url(f"/account/orders/{order.id}")

        context = {
            "order": order,
            "retry_url": retry_url,
            "SITE_NAME": settings.SITE_NAME,
            "INFO_EMAIL": tenant_contact_email(),
            "SITE_URL": get_tenant_base_url(),
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

        with translation.override(get_order_language(order)):
            subject = _("Payment Failed - Order #{order_id}").format(
                order_id=order.id
            )
            text_content = render_to_string(
                "emails/order/payment_failed.txt", context
            )
            html_content = render_to_string(
                "emails/order/payment_failed.html", context
            )

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            tenant_from_email(),
            [order.email],
            reply_to=[tenant_contact_email()],
            headers=build_transactional_list_headers(list_id="payment_failed"),
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()

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
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        if reserved_this_call:
            _release_payment_failed_email(order_id)
        return False


REFUND_CONFIRMATION_EMAIL_SENT_FLAG = "refund_confirmation_email_sent"


def _reserve_refund_confirmation_email(order_id: int) -> bool:
    """Atomically claim the refund-confirmation-email slot for an order.

    Both ``OrderService.refund_order`` (the in-app admin-initiated
    path) and ``handle_stripe_charge_refunded`` (the webhook path)
    fire ``order_refunded.send`` for the same order. Without this
    reservation, the customer would get two refund emails — one when
    we hit the Stripe API, another when Stripe redelivers the event
    back to us.

    Returns True if this caller won the race. Raises
    ``Order.DoesNotExist`` so the caller can surface a missing order
    consistently with the rest of the file.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.metadata and order.metadata.get(
            REFUND_CONFIRMATION_EMAIL_SENT_FLAG
        ):
            return False
        if not order.metadata:
            order.metadata = {}
        order.metadata[REFUND_CONFIRMATION_EMAIL_SENT_FLAG] = True
        order.save(update_fields=["metadata"])
    return True


def _release_refund_confirmation_email(order_id: int) -> None:
    """Clear the refund-confirmation-email reservation on permanent failure."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=order_id).first()
        if order is None or not order.metadata:
            return
        if (
            order.metadata.pop(REFUND_CONFIRMATION_EMAIL_SENT_FLAG, None)
            is not None
        ):
            order.save(update_fields=["metadata"])


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_refund_confirmation_email(self, order_id: int) -> bool:
    """Send the customer the refund-confirmation email.

    Triggered from ``handle_order_refunded`` so both refund paths
    converge here — admin-initiated refunds via
    ``OrderService.refund_order`` and Stripe-dashboard refunds
    arriving as ``charge.refunded`` webhooks.

    Renders ``emails/order/order_refunded.html`` (which inherits from
    ``order_status_generic.html``'s REFUNDED branch). Reuses the
    transactional ``List-Unsubscribe`` headers added in PR #4.
    """
    reserved_this_call = False
    try:
        if self.request.retries == 0:
            try:
                if not _reserve_refund_confirmation_email(order_id):
                    logger.info(
                        "Refund confirmation email already sent (or reserved) "
                        "for order #%s, skipping",
                        order_id,
                    )
                    return True
                reserved_this_call = True
            except Order.DoesNotExist:
                logger.error(
                    "Could not reserve refund confirmation email — Order #%s "
                    "not found",
                    order_id,
                    extra={"order_id": order_id},
                )
                return False

        order = Order.objects.select_related(
            "user", "country", "region", "pay_way"
        ).get(id=order_id)

        context = {
            "order": order,
            "status": "REFUNDED",
            "status_display": order.get_payment_status_display()
            if order.payment_status
            else "Refunded",
            "items": order.items.all(),
            "SITE_NAME": settings.SITE_NAME,
            "INFO_EMAIL": tenant_contact_email(),
            "SITE_URL": get_tenant_base_url(),
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

        with translation.override(get_order_language(order)):
            subject = _("Refund Processed - Order #{order_id}").format(
                order_id=order.id
            )
            text_content = render_to_string(
                "emails/order/order_refunded.txt", context
            )
            html_content = render_to_string(
                "emails/order/order_refunded.html", context
            )

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            tenant_from_email(),
            [order.email],
            reply_to=[tenant_contact_email()],
            headers=build_transactional_list_headers(
                list_id="refund_confirmation"
            ),
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()

        OrderHistory.log_note(
            order=order,
            note=f"Refund confirmation email sent to {order.email}",
        )

        logger.info(
            "Refund confirmation email sent for order #%s",
            order.id,
            extra={"order_id": order.id, "email": order.email},
        )
        return True

    except Order.DoesNotExist:
        logger.error(
            "Could not send refund confirmation email — Order #%s not found",
            order_id,
            extra={"order_id": order_id},
        )
        return False

    except Exception as e:
        logger.error(
            "Error sending refund confirmation email for order #%s: %s",
            order_id,
            e,
            extra={"order_id": order_id, "error": str(e)},
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        if reserved_this_call:
            _release_refund_confirmation_email(order_id)
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
    reserved_this_call = False
    try:
        # Only reserve on the first attempt to prevent the flag from
        # blocking legitimate retries after a transient failure.
        if self.request.retries == 0:
            if not _reserve_status_update_email(order_id, status):
                logger.info(
                    "Status update email already sent (or reserved) "
                    "for order #%s status=%s, skipping",
                    order_id,
                    status,
                )
                return True
            reserved_this_call = True

        order = (
            Order.objects.select_related("user", "country", "region", "pay_way")
            .prefetch_related("items__product__translations")
            .get(id=order_id)
        )

        if status in [OrderStatus.PENDING]:
            return True

        # Skip the generic status-update email when the transition is
        # PENDING → PROCESSING triggered by a successful payment. The
        # confirmation email (`order_payment_confirmed` template) is
        # sent separately by the webhook handler and is the
        # authoritative "payment received, now processing" notification.
        # Sending both produced a duplicate "Σε επεξεργασία" + "Payment
        # Confirmed" pair for every online order.
        if (
            status in (OrderStatus.PROCESSING, OrderStatus.PROCESSING.value)
            and order.payment_status == PaymentStatus.COMPLETED
        ):
            logger.info(
                "Order %s already has payment_status=COMPLETED; skipping "
                "PROCESSING status-update email (confirmation email covers it)",
                order.id,
            )
            return True

        context = {
            "order": order,
            "items": order.items.select_related("product").all(),
            "status": status,
            "status_display": OrderStatus(status).label,
            "SITE_NAME": settings.SITE_NAME,
            "INFO_EMAIL": tenant_contact_email(),
            "SITE_URL": get_tenant_base_url(),
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

        if status in (OrderStatus.SHIPPED, OrderStatus.SHIPPED.value):
            context["tracking_number"] = order.tracking_number
            context["carrier"] = order.shipping_carrier

        template_base = f"emails/order/order_{status.lower()}"

        with translation.override(get_order_language(order)):
            subject = _("Order #{order_id} Status Update - {status}").format(
                order_id=order.id, status=OrderStatus(status).label
            )

            try:
                text_content = render_to_string(f"{template_base}.txt", context)
                html_content = render_to_string(
                    f"{template_base}.html", context
                )
            except Exception:
                logger.warning(
                    f"Template {template_base} not found, using generic template",
                    extra={"order_id": order_id, "status": status},
                )
                text_content = render_to_string(
                    "emails/order/order_status_generic.txt", context
                )
                html_content = render_to_string(
                    "emails/order/order_status_generic.html", context
                )

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            tenant_from_email(),
            [order.email],
            reply_to=[tenant_contact_email()],
            headers=build_transactional_list_headers(list_id="order_status"),
        )
        msg.attach_alternative(html_content, "text/html")

        msg.send()

        logger.info(
            f"Order status update email sent for order #{order.id} - Status: {status}",
            extra={
                "order_id": order.id,
                "status": status,
                "email": order.email,
            },
        )

        OrderHistory.log_note(
            order=order,
            note=f"Status update email sent to {order.email} for {status} status",
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

        if self.request.retries < self.max_retries:
            logger.info(
                f"Retrying send_order_status_update_email for order #{order_id} "
                f"(attempt {self.request.retries + 1}/{self.max_retries + 1})"
            )
            raise self.retry(exc=e) from e

        if reserved_this_call:
            _release_status_update_email(order_id, status)

        return False


SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG = "shipping_notification_email_sent"


def _reserve_shipping_notification_email(order_id: int) -> bool:
    """Atomically claim the shipping-notification-email slot for an order.

    Mirrors ``_reserve_confirmation_email`` so concurrent fires of the
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


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_shipping_notification_email(self, order_id: int) -> bool:
    try:
        # Only reserve on the first attempt. Retries are expected to
        # re-send because the flag is held by this worker; releasing
        # on every retry would defeat idempotency, and re-checking
        # would block a legitimate retry after a transient failure.
        if self.request.retries == 0:
            try:
                if not _reserve_shipping_notification_email(order_id):
                    logger.info(
                        "Shipping notification email already sent (or reserved) "
                        "for order #%s, skipping",
                        order_id,
                    )
                    return True
            except Order.DoesNotExist:
                logger.error(
                    "Could not reserve shipping notification email — Order #%s "
                    "not found",
                    order_id,
                    extra={"order_id": order_id},
                )
                return False

        order = (
            Order.objects.select_related("user", "country", "region", "pay_way")
            .prefetch_related("items__product__translations")
            .get(id=order_id)
        )

        if not order.tracking_number or not order.shipping_carrier:
            logger.warning(
                f"Attempted to send shipping notification for order #{order_id} without tracking info",
                extra={"order_id": order_id},
            )
            return False

        context = {
            "order": order,
            "tracking_number": order.tracking_number,
            "carrier": order.shipping_carrier,
            "SITE_NAME": settings.SITE_NAME,
            "INFO_EMAIL": tenant_contact_email(),
            "SITE_URL": get_tenant_base_url(),
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

        with translation.override(get_order_language(order)):
            subject = _("Your Order #{order_id} Has Shipped").format(
                order_id=order.id
            )
            text_content = render_to_string(
                "emails/order/order_shipped.txt", context
            )
            html_content = render_to_string(
                "emails/order/order_shipped.html", context
            )

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            tenant_from_email(),
            [order.email],
            reply_to=[tenant_contact_email()],
            headers=build_transactional_list_headers(
                list_id="shipping_notification"
            ),
        )
        msg.attach_alternative(html_content, "text/html")

        msg.send()

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

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e

        return False


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def generate_order_invoice(self, order_id: int) -> bool:
    """Generate a PDF invoice for an order and persist it.

    Idempotent via ``order.invoicing.generate_invoice`` — calling this
    task twice for the same order is a no-op on the second call
    (returns the existing Invoice row). Safe to invoke from
    ``handle_order_completed`` without an explicit dedupe flag. Once
    the PDF is ready this task also schedules ``send_invoice_email``
    so the buyer gets a copy via email.
    """
    from order.invoicing import generate_invoice

    try:
        order = Order.objects.select_related(
            "country", "region", "pay_way"
        ).get(id=order_id)
    except Order.DoesNotExist:
        logger.error(
            "Could not generate invoice - Order #%s not found",
            order_id,
            extra={"order_id": order_id},
        )
        return False

    try:
        invoice = generate_invoice(order)
    except Exception as e:
        logger.error(
            "Error generating invoice for order #%s: %s",
            order_id,
            e,
            extra={"order_id": order_id, "error": str(e)},
            exc_info=True,
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        return False

    logger.info(
        "Invoice %s ready for order #%s", invoice.invoice_number, order_id
    )
    if not invoice.has_document():
        # An Invoice row without a file would just produce an empty
        # attachment / unusable myDATA submission. Bail out — the
        # next retry or an admin regeneration will re-trigger the chain.
        return True

    # Chain to myDATA submission when enabled; that task chains the
    # email itself once the MARK is persisted (so the attached PDF
    # carries the AADE MARK + QR). When myDATA is off or auto-submit
    # is disabled, email fires directly as before.
    from order.mydata.config import load_config as _load_mydata_config

    mydata_config = _load_mydata_config()
    if mydata_config.is_ready() and mydata_config.auto_submit:
        send_invoice_to_mydata.delay(order_id)
    else:
        send_invoice_email.delay(order_id)
    return True


INVOICE_EMAIL_SENT_FLAG = "invoice_email_sent"


def _reserve_invoice_email(order_id: int) -> bool:
    """Mirror of ``_reserve_confirmation_email`` for the invoice email.

    Returns ``True`` when this caller won the race. The flag lives
    under ``Order.metadata`` so it survives task retries and
    concurrent webhook bursts.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.metadata and order.metadata.get(INVOICE_EMAIL_SENT_FLAG):
            return False
        if not order.metadata:
            order.metadata = {}
        order.metadata[INVOICE_EMAIL_SENT_FLAG] = True
        order.save(update_fields=["metadata"])
    return True


def _release_invoice_email(order_id: int) -> None:
    """Clear the invoice-email reservation on permanent failure."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=order_id).first()
        if order is None or not order.metadata:
            return
        if order.metadata.pop(INVOICE_EMAIL_SENT_FLAG, None) is not None:
            order.save(update_fields=["metadata"])


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_invoice_email(self, order_id: int) -> bool:
    """Email the rendered invoice PDF to the buyer.

    Chained from ``generate_order_invoice`` once the PDF is ready.
    Idempotent via ``INVOICE_EMAIL_SENT_FLAG`` in ``Order.metadata`` so
    a retry or a re-fired ``order_completed`` signal doesn't re-send.
    Released on permanent failure so an admin can resend manually.
    """
    reserved_this_call = False
    try:
        if self.request.retries == 0:
            if not _reserve_invoice_email(order_id):
                logger.info(
                    "Invoice email already sent for order #%s, skipping",
                    order_id,
                )
                return True
            reserved_this_call = True

        order = Order.objects.select_related(
            "user", "country", "region", "pay_way"
        ).get(id=order_id)
        invoice = getattr(order, "invoice", None)
        if invoice is None or not invoice.has_document():
            # No PDF to attach — release the flag so a later generation
            # can trigger the email.
            _release_invoice_email(order_id)
            logger.warning(
                "Invoice email skipped for order #%s — PDF not ready",
                order_id,
            )
            return False

        with translation.override(get_order_language(order)):
            subject = _(
                "Invoice {invoice_number} for your order #{order_id}"
            ).format(invoice_number=invoice.invoice_number, order_id=order.id)
            context = {
                "order": order,
                "invoice": invoice,
                "SITE_NAME": settings.SITE_NAME,
                "INFO_EMAIL": tenant_contact_email(),
                "SITE_URL": get_tenant_base_url(),
                "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            }
            text_content = render_to_string(
                "emails/order/invoice_issued.txt", context
            )
            html_content = render_to_string(
                "emails/order/invoice_issued.html", context
            )

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            tenant_from_email(),
            [order.email],
            reply_to=[tenant_contact_email()],
            headers=build_transactional_list_headers(list_id="order_invoice"),
        )
        msg.attach_alternative(html_content, "text/html")

        # ``storage.open`` works for both S3 and FileSystem — streams
        # from S3 in prod, opens the file in dev. ``.read()`` is fine
        # at invoice sizes (≤ ~50kB typical).
        with invoice.document_file.open("rb") as fh:
            pdf_bytes = fh.read()
        msg.attach(
            f"{invoice.invoice_number}.pdf",
            pdf_bytes,
            "application/pdf",
        )

        msg.send()

        logger.info(
            "Invoice email sent for order #%s (%s)",
            order.id,
            invoice.invoice_number,
        )
        OrderHistory.log_note(
            order=order,
            note=f"Invoice {invoice.invoice_number} emailed to {order.email}",
        )
        return True

    except Order.DoesNotExist:
        logger.error(
            "Could not send invoice email - Order #%s not found",
            order_id,
        )
        return False

    except Exception as e:
        logger.error(
            "Error sending invoice email for order #%s: %s",
            order_id,
            e,
            extra={"order_id": order_id, "error": str(e)},
            exc_info=True,
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        if reserved_this_call:
            _release_invoice_email(order_id)
        return False


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=5, default_retry_delay=120
)
def send_invoice_to_mydata(self, order_id: int) -> bool:
    """Submit the order's invoice to AADE myDATA.

    Chained from :func:`generate_order_invoice` once the PDF is
    rendered AND the myDATA integration is enabled + auto-submit.
    On success: persists MARK + ``qr_url`` on the invoice, regenerates
    the PDF (so the final customer artifact carries the MARK + AADE-
    returned QR code), then chains :func:`send_invoice_email`.

    Retries transport-level failures with the SAME ``uid`` — AADE
    dedupes via error 228 so retries are idempotent. Terminal
    validation errors short-circuit to the email step with the
    pre-transmission PDF so the buyer still gets an invoice even
    when AADE rejected ours (ops reconciles via the REJECTED state
    in admin).
    """
    from order.invoicing import generate_invoice
    from order.mydata import (
        MyDataAuthError,
        MyDataDuplicateError,
        MyDataError,
        MyDataTransportError,
        MyDataValidationError,
        submit_invoice,
    )

    try:
        order = Order.objects.select_related(
            "user", "country", "region", "pay_way"
        ).get(id=order_id)
    except Order.DoesNotExist:
        logger.error(
            "Could not submit invoice to myDATA - Order #%s not found",
            order_id,
        )
        return False

    invoice = getattr(order, "invoice", None)
    if invoice is None or not invoice.has_document():
        logger.warning(
            "myDATA submission skipped for order #%s — invoice PDF not ready",
            order_id,
        )
        send_invoice_email.delay(order_id)
        return False

    try:
        response = submit_invoice(invoice)
    except MyDataTransportError as exc:
        # Transient: network, 5xx, 429. Retry with the SAME uid —
        # AADE dedupes via error 228 so repeats are safe.
        logger.warning(
            "myDATA submission transport failure for order #%s: %s",
            order_id,
            exc,
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        # Out of retries — fall through to email so the buyer still
        # gets the pre-transmission PDF; status stays SUBMITTED so
        # ops can retry manually once AADE is back.
        send_invoice_email.delay(order_id)
        return False
    except MyDataDuplicateError as exc:
        # AADE error 228: the same uid is already registered under
        # another MARK. Retrying will only loop — the response is
        # identical every time. Tier A.5 will call
        # ``RequestTransmittedDocs`` to recover the existing MARK and
        # flip the row to CONFIRMED; for now we log loud, leave
        # REJECTED state in place, and deliver the pre-transmission
        # PDF so the customer isn't blocked.
        logger.error(
            "myDATA submission rejected as duplicate for order #%s "
            "(uid=%s). Ops reconciliation needed via "
            "RequestTransmittedDocs: %s",
            order_id,
            invoice.mydata_uid,
            exc,
        )
        OrderHistory.log_note(
            order=order,
            note=(
                f"myDATA reported duplicate uid for invoice "
                f"{invoice.invoice_number} — manual reconciliation required "
                f"(query RequestTransmittedDocs with uid={invoice.mydata_uid})"
            ),
        )
        send_invoice_email.delay(order_id)
        return False
    except (MyDataValidationError, MyDataAuthError) as exc:
        logger.error(
            "myDATA submission terminal failure for order #%s (code=%s): %s",
            order_id,
            exc.code,
            exc.message,
        )
        OrderHistory.log_note(
            order=order,
            note=f"myDATA rejected invoice {invoice.invoice_number}: "
            f"{exc.code} {exc.message}",
        )
        send_invoice_email.delay(order_id)
        return False
    except MyDataError as exc:
        # Catch-all for future subclasses we haven't branched on yet.
        logger.error(
            "myDATA submission unexpected error for order #%s: %s",
            order_id,
            exc,
        )
        send_invoice_email.delay(order_id)
        return False

    if response is None:
        # Integration disabled mid-flight — deliver the pre-transmission
        # PDF rather than leaving the order stuck.
        send_invoice_email.delay(order_id)
        return True

    # Success: MARK + qr_url persisted. Re-render the PDF so the
    # version the customer downloads carries the authoritative AADE
    # QR (served from AADE's verification portal). ``force=True``
    # preserves ``invoice_number`` + ``issue_date`` (no counter gap).
    try:
        generate_invoice(order, force=True)
    except Exception as exc:  # noqa: BLE001 — never block the email
        logger.error(
            "Failed to re-render PDF with MARK for order #%s: %s",
            order_id,
            exc,
            exc_info=True,
        )

    OrderHistory.log_note(
        order=order,
        note=f"Invoice {invoice.invoice_number} registered with myDATA "
        f"(MARK={response.invoice_mark})",
    )
    send_invoice_email.delay(order_id)
    return True


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def cancel_mydata_invoice(self, order_id: int) -> bool:
    """Send a ``CancelInvoice`` for the order's registered invoice.

    Fails fast when there is no MARK — cancelling a document that
    was never transmitted is a no-op, not an error. Only used from
    the admin action today; automatic cancellation (e.g. on order
    refund) is not wired until Tier B.
    """
    from order.mydata import (
        MyDataError,
        MyDataTransportError,
        cancel_invoice as _cancel,
    )

    try:
        order = Order.objects.select_related("user").get(id=order_id)
    except Order.DoesNotExist:
        logger.error(
            "Could not cancel myDATA invoice - Order #%s not found", order_id
        )
        return False

    invoice = getattr(order, "invoice", None)
    if invoice is None or not invoice.mydata_mark:
        logger.warning(
            "myDATA cancellation skipped for order #%s — no MARK on file",
            order_id,
        )
        return False

    try:
        response = _cancel(invoice)
    except MyDataTransportError as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        logger.error(
            "myDATA cancellation exhausted retries for order #%s: %s",
            order_id,
            exc,
        )
        return False
    except MyDataError as exc:
        logger.error(
            "myDATA cancellation terminal failure for order #%s: %s",
            order_id,
            exc,
        )
        return False

    if response is None:
        # Integration disabled — treat as no-op success so the admin
        # action doesn't loop on retries.
        return True

    OrderHistory.log_note(
        order=order,
        note=f"Invoice {invoice.invoice_number} cancelled in myDATA "
        f"(cancellation MARK={response.cancellation_mark})",
    )
    return True


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def check_pending_orders() -> int:
    try:
        now = timezone.now()
        one_day_ago = now - timedelta(days=1)
        max_reminders = Setting.get(
            "PENDING_ORDER_REMINDER_MAX_COUNT", default=3
        )
        reminder_intervals = [
            Setting.get(
                f"PENDING_ORDER_REMINDER_INTERVAL_DAYS_{i}",
                default=d,
            )
            for i, d in [(1, 1), (2, 3), (3, 7)]
        ]
        pending_orders = Order.objects.filter(
            status=OrderStatus.PENDING,
            created_at__lt=one_day_ago,
            reminder_count__lt=max_reminders,
        )

        count = 0
        for order in pending_orders:
            if order.last_reminder_sent_at:
                interval_index = min(
                    order.reminder_count,
                    len(reminder_intervals) - 1,
                )
                cooldown = timedelta(days=reminder_intervals[interval_index])
                if now - order.last_reminder_sent_at < cooldown:
                    continue

            context = {
                "order": order,
                "SITE_NAME": settings.SITE_NAME,
                "INFO_EMAIL": tenant_contact_email(),
                "SITE_URL": get_tenant_base_url(),
                "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            }

            with translation.override(get_order_language(order)):
                subject = _("Reminder: Complete Your Order #{order_id}").format(
                    order_id=order.id
                )
                text_content = render_to_string(
                    "emails/order/order_pending_reminder.txt", context
                )
                html_content = render_to_string(
                    "emails/order/order_pending_reminder.html", context
                )

            msg = EmailMultiAlternatives(
                subject,
                text_content,
                tenant_from_email(),
                [order.email],
                reply_to=[tenant_contact_email()],
            )
            msg.attach_alternative(html_content, "text/html")

            msg.send()

            Order.objects.filter(pk=order.pk).update(
                reminder_count=F("reminder_count") + 1,
                last_reminder_sent_at=now,
            )

            logger.info(
                f"Pending order reminder sent for order #{order.id} "
                f"(reminder {order.reminder_count + 1}/{max_reminders})",
                extra={"order_id": order.id, "email": order.email},
            )

            OrderHistory.log_note(
                order=order,
                note=f"Pending order reminder email sent to {order.email} "
                f"({order.reminder_count + 1}/{max_reminders})",
            )

            count += 1

        return count

    except Exception as e:
        logger.error(
            f"Error checking pending orders: {e!s}",
            extra={"error": str(e)},
        )
        return 0


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=(Exception,),
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
        from order.stock import StockManager

        count = StockManager.cleanup_expired_reservations()

        if count > 0:
            logger.info(
                f"Cleaned up {count} expired stock reservations",
                extra={"cleaned_count": count},
            )

        return count

    except Exception as e:
        logger.error(
            f"Error cleaning up expired stock reservations: {e!s}",
            extra={"error": str(e)},
            exc_info=True,
        )
        return 0


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def auto_cancel_stuck_pending_orders() -> dict[str, int]:
    """Auto-cancel PENDING orders that will never be paid.

    Two buckets:
    - Orders with FAILED payment older than
      ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES (default 30)
    - Orders with PENDING payment older than
      ORDER_AUTO_CANCEL_PENDING_HOURS (default 24) — covers the case
      where the customer abandoned online checkout without triggering
      any webhook.

    Delegates to OrderService.cancel_order which restores stock via
    StockManager.increment_stock and releases any outstanding
    StockReservation records.
    """
    failed_minutes = Setting.get(
        "ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES", default=30
    )
    pending_hours = Setting.get("ORDER_AUTO_CANCEL_PENDING_HOURS", default=24)

    now = timezone.now()
    failed_cutoff = now - timedelta(minutes=failed_minutes)
    pending_cutoff = now - timedelta(hours=pending_hours)

    failed_qs = Order.objects.filter(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.FAILED,
        updated_at__lt=failed_cutoff,
    )
    # Only auto-cancel online-payment orders — offline methods (COD,
    # bank transfer) are expected to sit PENDING until the customer
    # pays, sometimes for days. Positive filter also excludes orders
    # with no pay_way at all (NULL) which are treated as
    # offline-equivalent for this bucket.
    pending_qs = Order.objects.filter(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        created_at__lt=pending_cutoff,
        pay_way__is_online_payment=True,
    )

    canceled_failed = 0
    canceled_pending = 0
    errors = 0

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
            )
            return True

    for order in failed_qs.iterator():
        try:
            if _cancel_with_lock(
                order,
                "Auto-canceled: payment failed and not retried",
            ):
                canceled_failed += 1
        except Exception as e:
            errors += 1
            logger.error(
                "Auto-cancel (failed-payment) error for order %s: %s",
                order.id,
                e,
                exc_info=True,
            )

    for order in pending_qs.iterator():
        try:
            if _cancel_with_lock(
                order,
                "Auto-canceled: payment never completed",
            ):
                canceled_pending += 1
        except Exception as e:
            errors += 1
            logger.error(
                "Auto-cancel (stale-pending) error for order %s: %s",
                order.id,
                e,
                exc_info=True,
            )

    total = canceled_failed + canceled_pending
    if total or errors:
        logger.info(
            "auto_cancel_stuck_pending_orders: canceled_failed=%s canceled_pending=%s errors=%s",
            canceled_failed,
            canceled_pending,
            errors,
        )
    return {
        "canceled_failed": canceled_failed,
        "canceled_pending": canceled_pending,
        "errors": errors,
    }


@celery_app.task(
    base=MonitoredTask,
    autoretry_for=(Exception,),
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

    hours = Setting.get("CHECKOUT_ABANDONMENT_HOURS", default=2)
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

    carts = (
        Cart.objects.select_related("user")
        .filter(uuid__in=abandoned_session_ids, user__isnull=False)
        .prefetch_related("items__product__translations")
    )

    sent = 0
    for cart in carts:
        if not cart.user or not cart.user.email:
            continue
        try:
            uid = urlsafe_base64_encode(force_bytes(cart.user.pk))
            token = default_token_generator.make_token(cart.user)
            unsubscribe_url = (
                f"{settings.API_BASE_URL.rstrip('/')}/api/v1/user/unsubscribe/{uid}/{token}"
                if getattr(settings, "API_BASE_URL", None)
                else ""
            )

            context = {
                "cart": cart,
                "items": list(cart.items.all()),
                # Point the CTA at the recovery route — the Nuxt page
                # there re-attaches the cart to the session (including
                # the logged-out → login → back round-trip via the
                # global auth middleware) and forwards to /cart with a
                # ``recovered=1`` flag so the shopper sees a welcome
                # banner instead of landing silently on their items.
                "cart_url": get_tenant_frontend_url(
                    f"/cart/recover/{cart.uuid}"
                ),
                "preferences_url": get_tenant_frontend_url(
                    "/account/subscriptions/"
                ),
                "unsubscribe_url": unsubscribe_url,
                "SITE_NAME": settings.SITE_NAME,
                "INFO_EMAIL": tenant_contact_email(),
                "SITE_URL": get_tenant_base_url(),
                "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            }
            with translation.override(get_user_language(cart.user)):
                subject = _("Did you forget something? — {site_name}").format(
                    site_name=settings.SITE_NAME
                )
                text_content = render_to_string(
                    "emails/cart/checkout_abandoned.txt", context
                )
                html_content = render_to_string(
                    "emails/cart/checkout_abandoned.html", context
                )
            headers = {"List-ID": f"abandoned-cart.{settings.SITE_NAME}"}
            if unsubscribe_url:
                headers["List-Unsubscribe"] = (
                    f"<mailto:{tenant_contact_email()}?subject=unsubscribe>, "
                    f"<{unsubscribe_url}>"
                )
                headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            msg = EmailMultiAlternatives(
                subject,
                text_content,
                tenant_from_email(),
                [cart.user.email],
                reply_to=[tenant_contact_email()],
                headers=headers,
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send()

            StockReservation.objects.filter(session_id=str(cart.uuid)).update(
                abandonment_notified=True
            )
            sent += 1
        except Exception as e:
            logger.error(
                "Error sending checkout-abandonment email for cart %s: %s",
                cart.id,
                e,
                exc_info=True,
            )

    if sent:
        logger.info("Sent %s checkout-abandonment emails", sent)
    return sent
