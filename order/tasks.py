import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from extra_settings.models import Setting

from order.enum.status import OrderStatus, PaymentStatus
from order.models import Order, OrderHistory
from order.services import OrderService
from order.shipping import ShippingService

logger = logging.getLogger(__name__)


CONFIRMATION_EMAIL_SENT_FLAG = "confirmation_email_sent"


def _reserve_confirmation_email(order_id: int) -> bool:
    """Atomically claim the confirmation-email slot for an order.

    Returns True if this caller won the race and should proceed with the
    send, False if another caller has already sent (or is sending) the
    email. Raises Order.DoesNotExist if the order is missing so the
    caller can surface it consistently with the rest of the task.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.metadata and order.metadata.get(CONFIRMATION_EMAIL_SENT_FLAG):
            return False
        if not order.metadata:
            order.metadata = {}
        order.metadata[CONFIRMATION_EMAIL_SENT_FLAG] = True
        order.save(update_fields=["metadata"])
    return True


def _release_confirmation_email(order_id: int) -> None:
    """Clear the confirmation-email reservation on permanent failure so an
    admin (or a future retry path) can resend the email."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(id=order_id).first()
        if order is None or not order.metadata:
            return
        if order.metadata.pop(CONFIRMATION_EMAIL_SENT_FLAG, None) is not None:
            order.save(update_fields=["metadata"])


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def send_order_confirmation_email(self, order_id: int) -> bool:
    reserved_this_call = False
    try:
        # Only reserve on the first attempt. Retries are expected to
        # re-send because the flag is held by THIS worker — releasing
        # on every retry would defeat idempotency, and checking the
        # flag on retry would prevent legitimate re-sends after a
        # transient failure.
        if self.request.retries == 0:
            if not _reserve_confirmation_email(order_id):
                logger.info(
                    "Order confirmation email already sent (or reserved) for order #%s, skipping",
                    order_id,
                )
                return True
            reserved_this_call = True

        order = (
            Order.objects.select_related("user", "country", "region", "pay_way")
            .prefetch_related(
                "items__product__translations", "pay_way__translations"
            )
            .get(id=order_id)
        )

        pay_way = order.pay_way
        is_paid = bool(
            pay_way
            and pay_way.is_online_payment
            and order.payment_status == PaymentStatus.COMPLETED
        )
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
            "INFO_EMAIL": settings.INFO_EMAIL,
            "SITE_URL": settings.NUXT_BASE_URL,
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

        text_content = render_to_string(f"{template_base}.txt", context)
        html_content = render_to_string(f"{template_base}.html", context)

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            [order.email],
            reply_to=[settings.INFO_EMAIL],
        )
        msg.attach_alternative(html_content, "text/html")

        msg.send()

        logger.info(
            f"Order confirmation email sent for order #{order.id}",
            extra={"order_id": order.id, "email": order.email},
        )

        OrderHistory.log_note(
            order=order,
            note=f"Order confirmation email sent to {order.email}",
        )

        return True

    except Order.DoesNotExist:
        logger.error(
            f"Could not send confirmation email - Order #{order_id} not found",
            extra={"order_id": order_id},
        )
        return False

    except Exception as e:
        logger.error(
            f"Error sending order confirmation email for order #{order_id}: {e!s}",
            extra={"order_id": order_id, "error": str(e)},
        )

        retry_count = self.request.retries
        max_retries = self.max_retries

        if retry_count < max_retries:
            logger.info(
                f"Retrying send_order_confirmation_email for order #{order_id} "
                f"(attempt {retry_count + 1}/{max_retries + 1})"
            )
            raise self.retry(exc=e) from e

        if reserved_this_call:
            _release_confirmation_email(order_id)

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


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
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

        retry_url = (
            f"{settings.NUXT_BASE_URL.rstrip('/')}/account/orders/{order.id}"
            if getattr(settings, "NUXT_BASE_URL", None)
            else ""
        )

        context = {
            "order": order,
            "retry_url": retry_url,
            "SITE_NAME": settings.SITE_NAME,
            "INFO_EMAIL": settings.INFO_EMAIL,
            "SITE_URL": settings.NUXT_BASE_URL,
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

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
            settings.DEFAULT_FROM_EMAIL,
            [order.email],
            reply_to=[settings.INFO_EMAIL],
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


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def send_order_status_update_email(
    self, order_id: int, status: OrderStatus
) -> bool:
    try:
        order = (
            Order.objects.select_related("user", "country", "region", "pay_way")
            .prefetch_related("items__product__translations")
            .get(id=order_id)
        )

        if status in [OrderStatus.PENDING]:
            return True

        context = {
            "order": order,
            "items": order.items.select_related("product").all(),
            "status": status,
            "status_display": OrderStatus(status).label,
            "SITE_NAME": settings.SITE_NAME,
            "INFO_EMAIL": settings.INFO_EMAIL,
            "SITE_URL": settings.NUXT_BASE_URL,
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

        if status in (OrderStatus.SHIPPED, OrderStatus.SHIPPED.value):
            context["tracking_number"] = order.tracking_number
            context["carrier"] = order.shipping_carrier

        template_base = f"emails/order/order_{status.lower()}"

        subject = _("Order #{order_id} Status Update - {status}").format(
            order_id=order.id, status=OrderStatus(status).label
        )

        try:
            text_content = render_to_string(f"{template_base}.txt", context)
            html_content = render_to_string(f"{template_base}.html", context)
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
            settings.DEFAULT_FROM_EMAIL,
            [order.email],
            reply_to=[settings.INFO_EMAIL],
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

        return False


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def send_shipping_notification_email(self, order_id: int) -> bool:
    try:
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
            "INFO_EMAIL": settings.INFO_EMAIL,
            "SITE_URL": settings.NUXT_BASE_URL,
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

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
            settings.DEFAULT_FROM_EMAIL,
            [order.email],
            reply_to=[settings.INFO_EMAIL],
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


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def generate_order_invoice(self, order_id: int) -> bool:
    try:
        Order.objects.get(id=order_id)

        # TODO: Generate a PDF invoice using weasyprint or reportlab,
        # store it in a file field on the Order model or a separate Invoice model.
        raise NotImplementedError("Invoice generation not yet implemented")

    except Order.DoesNotExist:
        logger.error(
            f"Could not generate invoice - Order #{order_id} not found",
            extra={"order_id": order_id},
        )
        return False

    except NotImplementedError:
        raise

    except Exception as e:
        logger.error(
            f"Error generating invoice for order #{order_id}: {e!s}",
            extra={"order_id": order_id, "error": str(e)},
        )

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e

        return False


@shared_task
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
                "INFO_EMAIL": settings.INFO_EMAIL,
                "SITE_URL": settings.NUXT_BASE_URL,
                "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            }

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
                settings.DEFAULT_FROM_EMAIL,
                [order.email],
                reply_to=[settings.INFO_EMAIL],
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


@shared_task
def update_order_statuses_from_shipping() -> int:
    try:
        shipped_orders = Order.objects.filter(
            status=OrderStatus.SHIPPED, tracking_number__isnull=False
        ).exclude(tracking_number="")

        count = 0

        for order in shipped_orders:
            if not order.shipping_carrier:
                continue

            try:
                tracking_info = ShippingService.get_tracking_info(
                    order.tracking_number, order.shipping_carrier
                )

                if tracking_info.get("status") == OrderStatus.DELIVERED:
                    OrderService.update_order_status(
                        order, OrderStatus.DELIVERED
                    )
                    # Email is sent by handle_order_status_changed signal
                    count += 1

            except Exception as inner_e:
                logger.error(
                    f"Error updating shipping status for order #{order.id}: {inner_e!s}",
                    extra={"order_id": order.id, "error": str(inner_e)},
                )

        return count

    except Exception as e:
        logger.error(
            f"Error updating order statuses from shipping: {e!s}",
            extra={"error": str(e)},
        )
        return 0


@shared_task
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


@shared_task
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

    for order in failed_qs.iterator():
        try:
            OrderService.cancel_order(
                order,
                reason="Auto-canceled: payment failed and not retried",
                refund_payment=False,
            )
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
            OrderService.cancel_order(
                order,
                reason="Auto-canceled: payment never completed",
                refund_payment=False,
            )
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


@shared_task
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
            context = {
                "cart": cart,
                "items": list(cart.items.all()),
                "cart_url": (
                    f"{settings.NUXT_BASE_URL.rstrip('/')}/cart"
                    if getattr(settings, "NUXT_BASE_URL", None)
                    else ""
                ),
                "SITE_NAME": settings.SITE_NAME,
                "INFO_EMAIL": settings.INFO_EMAIL,
                "SITE_URL": settings.NUXT_BASE_URL,
                "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            }
            subject = _("Did you forget something? — {site_name}").format(
                site_name=settings.SITE_NAME
            )
            text_content = render_to_string(
                "emails/cart/checkout_abandoned.txt", context
            )
            html_content = render_to_string(
                "emails/cart/checkout_abandoned.html", context
            )
            msg = EmailMultiAlternatives(
                subject,
                text_content,
                settings.DEFAULT_FROM_EMAIL,
                [cart.user.email],
                reply_to=[settings.INFO_EMAIL],
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
