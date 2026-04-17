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

from order.enum.status import OrderStatus
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
            .prefetch_related("items__product__translations")
            .get(id=order_id)
        )
        context = {
            "order": order,
            "items": order.items.all(),
            "SITE_NAME": settings.SITE_NAME,
            "INFO_EMAIL": settings.INFO_EMAIL,
            "SITE_URL": settings.NUXT_BASE_URL,
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

        subject = _("Order Confirmation - #{order_id}").format(
            order_id=order.id
        )
        text_content = render_to_string(
            "emails/order/order_confirmation.txt", context
        )
        html_content = render_to_string(
            "emails/order/order_confirmation.html", context
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
    - expires_at < current_time (past the 15-minute TTL)
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
