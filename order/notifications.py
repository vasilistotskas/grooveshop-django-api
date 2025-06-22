import logging
from abc import ABC, abstractmethod

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from order.models.order import Order

logger = logging.getLogger(__name__)


class NotifierInterface(ABC):
    @abstractmethod
    def send_order_confirmation(self, order: Order) -> bool:
        pass

    @abstractmethod
    def send_order_shipped(
        self, order: Order, tracking_number: str, carrier: str
    ) -> bool:
        pass

    @abstractmethod
    def send_order_delivered(self, order: Order) -> bool:
        pass

    @abstractmethod
    def send_order_canceled(self, order: Order) -> bool:
        pass


class EmailNotifier(NotifierInterface):
    def send_order_confirmation(self, order: Order) -> bool:
        subject = _("Order Confirmation #{order_id}").format(order_id=order.id)
        context = {
            "order": order,
            "items": order.items.all(),
        }
        html_message = render_to_string(
            "emails/order_confirmation.html", context
        )
        text_message = render_to_string(
            "emails/order_confirmation.txt", context
        )

        try:
            send_mail(
                subject=subject,
                message=text_message,
                html_message=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[order.email],
                fail_silently=False,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send order confirmation email: {e!s}")
            return False

    def send_order_shipped(
        self, order: Order, tracking_number: str, carrier: str
    ) -> bool:
        subject = _("Your Order #{order_id} Has Shipped").format(
            order_id=order.id
        )
        context = {
            "order": order,
            "tracking_number": tracking_number,
            "carrier": carrier,
        }
        html_message = render_to_string("emails/order_shipped.html", context)
        text_message = render_to_string("emails/order_shipped.txt", context)

        try:
            send_mail(
                subject=subject,
                message=text_message,
                html_message=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[order.email],
                fail_silently=False,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send order shipped email: {e!s}")
            return False

    def send_order_delivered(self, order: Order) -> bool:
        subject = _("Your Order #{order_id} Has Been Delivered").format(
            order_id=order.id
        )
        context = {
            "order": order,
        }
        html_message = render_to_string("emails/order_delivered.html", context)
        text_message = render_to_string("emails/order_delivered.txt", context)

        try:
            send_mail(
                subject=subject,
                message=text_message,
                html_message=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[order.email],
                fail_silently=False,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send order delivered email: {e!s}")
            return False

    def send_order_canceled(self, order: Order) -> bool:
        subject = _("Your Order #{order_id} Has Been Canceled").format(
            order_id=order.id
        )
        context = {
            "order": order,
        }
        html_message = render_to_string("emails/order_canceled.html", context)
        text_message = render_to_string("emails/order_canceled.txt", context)

        try:
            send_mail(
                subject=subject,
                message=text_message,
                html_message=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[order.email],
                fail_silently=False,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send order canceled email: {e!s}")
            return False


class SMSNotifier(NotifierInterface):
    def send_order_confirmation(self, order: Order) -> bool:
        if not order.phone:
            return False

        message = _(
            "Thank you for your order #{order_id}. Your order total is {total_price}. We'll notify you when your order ships."
        ).format(order_id=order.id, total_price=order.total_price)

        try:
            # @TODO - Call SMS provider API here
            print(message)
            return True
        except Exception as e:
            logger.error(f"Failed to send order confirmation SMS: {e!s}")
            return False

    def send_order_shipped(
        self, order: Order, tracking_number: str = "", carrier: str = ""
    ) -> bool:
        if not order.phone:
            return False

        message = _(
            "Your order #{order_id} has shipped! Track it with {carrier} using tracking number {tracking_number}."
        ).format(
            order_id=order.id, carrier=carrier, tracking_number=tracking_number
        )

        try:
            # @TODO - Call SMS provider API here
            print(message)
            return True
        except Exception as e:
            logger.error(f"Failed to send order shipped SMS: {e!s}")
            return False

    def send_order_delivered(self, order: Order) -> bool:
        if not order.phone:
            return False

        message = _(
            "Your order #{order_id} has been delivered! Thank you for shopping with us."
        ).format(order_id=order.id)

        try:
            # @TODO - Call SMS provider API here
            print(message)
            return True
        except Exception as e:
            logger.error(f"Failed to send order delivered SMS: {e!s}")
            return False

    def send_order_canceled(self, order: Order) -> bool:
        if not order.phone:
            return False

        message = _(
            "Your order #{order_id} has been canceled. Please contact customer service if you have any questions."
        ).format(order_id=order.id)

        try:
            # @TODO - Call SMS provider API here
            print(message)
            return True
        except Exception as e:
            logger.error(f"Failed to send order canceled SMS: {e!s}")
            return False


class OrderNotificationManager:
    def __init__(self):
        self.email_notifier = EmailNotifier()
        self.sms_notifier = SMSNotifier()

    def send_order_confirmation(self, order: Order) -> bool:
        email_sent = self.email_notifier.send_order_confirmation(order)

        sms_sent = False
        if order.phone:
            sms_sent = self.sms_notifier.send_order_confirmation(order)

        return email_sent or sms_sent

    def send_order_shipped(
        self, order: Order, tracking_number: str, carrier: str
    ) -> bool:
        email_sent = self.email_notifier.send_order_shipped(
            order, tracking_number, carrier
        )

        sms_sent = False
        if order.phone:
            sms_sent = self.sms_notifier.send_order_shipped(
                order, tracking_number=tracking_number, carrier=carrier
            )

        return email_sent or sms_sent

    def send_order_delivered(self, order: Order) -> bool:
        email_sent = self.email_notifier.send_order_delivered(order)

        sms_sent = False
        if order.phone:
            sms_sent = self.sms_notifier.send_order_delivered(order)

        return email_sent or sms_sent

    def send_order_canceled(self, order: Order) -> bool:
        email_sent = self.email_notifier.send_order_canceled(order)

        sms_sent = False
        if order.phone:
            sms_sent = self.sms_notifier.send_order_canceled(order)

        return email_sent or sms_sent


def send_order_confirmation(order: Order) -> bool:
    manager = OrderNotificationManager()
    return manager.send_order_confirmation(order)


def send_order_shipped_notification(order: Order) -> bool:
    manager = OrderNotificationManager()
    return manager.send_order_shipped(
        order,
        tracking_number=order.tracking_number,
        carrier=order.shipping_carrier,
    )


def send_order_delivered_notification(order: Order) -> bool:
    manager = OrderNotificationManager()
    return manager.send_order_delivered(order)


def send_order_canceled_notification(order: Order) -> bool:
    manager = OrderNotificationManager()
    return manager.send_order_canceled(order)
