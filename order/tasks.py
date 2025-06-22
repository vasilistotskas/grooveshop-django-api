import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from order.enum.status import OrderStatus
from order.models import Order, OrderHistory
from order.services import OrderService
from order.shipping import ShippingService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def send_order_confirmation_email(self, order_id: int) -> bool:
    try:
        order = Order.objects.get(id=order_id)
        context = {
            "order": order,
            "items": order.items.all(),
            "site_name": getattr(settings, "SITE_NAME", "Our Shop"),
            "info_email": getattr(
                settings, "INFO_EMAIL", "support@example.com"
            ),
            "site_url": getattr(settings, "SITE_URL", "https://example.com"),
        }

        subject = _("Order Confirmation - #{order_id}").format(
            order_id=order.id
        )
        text_content = render_to_string(
            "emails/order_confirmation.txt", context
        )
        html_content = render_to_string(
            "emails/order_confirmation.html", context
        )

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@example.com"),
            [order.email],
            reply_to=[getattr(settings, "INFO_EMAIL", "support@example.com")],
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

        return False


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def send_order_status_update_email(
    self, order_id: int, status: OrderStatus
) -> bool:
    try:
        order = Order.objects.get(id=order_id)

        if status in [OrderStatus.PENDING]:
            return True

        context = {
            "order": order,
            "status": status,
            "site_name": getattr(settings, "SITE_NAME", "Our Shop"),
            "info_email": getattr(
                settings, "INFO_EMAIL", "support@example.com"
            ),
            "site_url": getattr(settings, "SITE_URL", "https://example.com"),
        }

        template_base = f"emails/order_{status.lower()}"

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
                "emails/order_status_generic.txt", context
            )
            html_content = render_to_string(
                "emails/order_status_generic.html", context
            )

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@example.com"),
            [order.email],
            reply_to=[getattr(settings, "INFO_EMAIL", "support@example.com")],
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
        order = Order.objects.get(id=order_id)

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
            "site_name": getattr(settings, "SITE_NAME", "Our Shop"),
            "info_email": getattr(
                settings, "INFO_EMAIL", "support@example.com"
            ),
            "site_url": getattr(settings, "SITE_URL", "https://example.com"),
        }

        subject = _("Your Order #{order_id} Has Shipped").format(
            order_id=order.id
        )
        text_content = render_to_string("emails/order_shipped.txt", context)
        html_content = render_to_string("emails/order_shipped.html", context)

        msg = EmailMultiAlternatives(
            subject,
            text_content,
            getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@example.com"),
            [order.email],
            reply_to=[getattr(settings, "INFO_EMAIL", "support@example.com")],
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


@shared_task
def generate_order_invoice(order_id: int) -> bool:
    try:
        order = Order.objects.get(id=order_id)

        # @TODO - In a real implementation, you would generate a PDF using a library
        # like weasyprint or reportlab, and store it in a file field on the Order model
        # or in a separate Invoice model

        # For this example, we'll just log the action
        logger.info(
            f"Invoice generated for order #{order.id}",
            extra={"order_id": order.id},
        )

        OrderHistory.log_note(
            order=order,
            note=f"Invoice generated for order #{order.id}",
        )

        return True

    except Order.DoesNotExist:
        logger.error(
            f"Could not generate invoice - Order #{order_id} not found",
            extra={"order_id": order_id},
        )
        return False

    except Exception as e:
        logger.error(
            f"Error generating invoice for order #{order_id}: {e!s}",
            extra={"order_id": order_id, "error": str(e)},
        )
        return False


@shared_task
def check_pending_orders() -> int:
    try:
        one_day_ago = timezone.now() - timedelta(days=1)
        pending_orders = Order.objects.filter(
            status=OrderStatus.PENDING, created_at__lt=one_day_ago
        )

        count = 0
        for order in pending_orders:
            context = {
                "order": order,
                "site_name": settings.SITE_NAME,
                "info_email": settings.INFO_EMAIL,
                "site_url": settings.SITE_URL,
            }

            subject = _("Reminder: Complete Your Order #{order_id}").format(
                order_id=order.id
            )
            text_content = render_to_string(
                "emails/order_pending_reminder.txt", context
            )
            html_content = render_to_string(
                "emails/order_pending_reminder.html", context
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
                f"Pending order reminder sent for order #{order.id}",
                extra={"order_id": order.id, "email": order.email},
            )

            OrderHistory.log_note(
                order=order,
                note=f"Pending order reminder email sent to {order.email}",
            )

            count += 1

        return count

    except Exception as e:
        logger.error(
            f"Error checking pending orders: {e!s}", extra={"error": str(e)}
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

                    send_order_status_update_email.delay(
                        order.id, OrderStatus.DELIVERED
                    )

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
