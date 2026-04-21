"""Live (in-app + WebSocket) notification dispatch tasks for orders.

Kept in a dedicated module so the email tasks in ``order/tasks.py`` and
the live-notification tasks don't read as one concern. The two worlds
share no code today — email tasks render templates and touch
``EmailMultiAlternatives``; live-notification tasks call
``notification.services.create_user_notification`` which transparently
writes a Notification + NotificationUser row and the post_save signal
in ``notification/signals.py`` fans out via ``send_notification_task``
to the per-user WebSocket group.

Idempotency note: the underlying notification helper is not idempotent
(two calls → two Notification rows). Signal receivers guard against
double-dispatch at the event level (e.g. the ``_paid_signal_sent`` flag
on ``order_paid``); at the Celery boundary these tasks rely on that.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.utils.translation import gettext as _
from django.utils.translation import override as translation_override

from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationTypeEnum,
)
from notification.services import create_user_notification
from order.enum.status import OrderStatus
from order.models.order import Order

logger = logging.getLogger(__name__)


TASK_DEFAULTS = {
    "bind": True,
    "max_retries": 3,
    "autoretry_for": (Exception,),
    "retry_backoff": True,
    "retry_jitter": True,
}


# Status → (category, kind, notification_type, i18n (title_msgid, message_msgid))
# Only transitions worth surfacing live are declared here. PENDING is
# covered separately by the ``order_created`` task (the initial "thanks,
# we got your order" moment). All message strings go through gettext so
# they end up in ``locale/*/LC_MESSAGES/django.po`` alongside the email
# copy and can be reviewed by the same translator pass.
_ORDER_STATUS_COPY: dict[str, tuple[str, str, str, tuple[str, str]]] = {
    OrderStatus.PROCESSING.value: (
        NotificationCategoryEnum.ORDER,
        NotificationKindEnum.INFO,
        NotificationTypeEnum.ORDER_PROCESSING,
        (
            "Order #{order_id} is being prepared",
            "We're getting your items ready — you'll hear from us again once "
            "they're on the way.",
        ),
    ),
    OrderStatus.SHIPPED.value: (
        NotificationCategoryEnum.SHIPPING,
        NotificationKindEnum.INFO,
        NotificationTypeEnum.ORDER_SHIPPED,
        (
            "Order #{order_id} is on its way!",
            "Your order has shipped. Tracking details will appear in the "
            "order page shortly.",
        ),
    ),
    OrderStatus.DELIVERED.value: (
        NotificationCategoryEnum.SHIPPING,
        NotificationKindEnum.SUCCESS,
        NotificationTypeEnum.ORDER_DELIVERED,
        (
            "Order #{order_id} delivered",
            "Your order has arrived. We'd love to hear what you think — "
            "reviews help other shoppers.",
        ),
    ),
    OrderStatus.COMPLETED.value: (
        NotificationCategoryEnum.ORDER,
        NotificationKindEnum.SUCCESS,
        NotificationTypeEnum.ORDER_COMPLETED,
        (
            "Order #{order_id} complete",
            "Thanks for shopping with us. Your loyalty points have been "
            "credited to your account.",
        ),
    ),
    OrderStatus.CANCELED.value: (
        NotificationCategoryEnum.ORDER,
        NotificationKindEnum.WARNING,
        NotificationTypeEnum.ORDER_CANCELED,
        (
            "Order #{order_id} canceled",
            "Your order has been canceled. Any payment taken will be "
            "refunded to the original method.",
        ),
    ),
}


def _order_link(order: Order) -> str:
    """Absolute URL pointing at the shopper's order detail page."""
    base = (settings.NUXT_BASE_URL or "").rstrip("/")
    return f"{base}/account/orders/{order.id}"


def _render_translations(
    title_msgid: str,
    message_msgid: str,
    **format_kwargs,
) -> dict[str, dict[str, str]]:
    """Build the ``translations`` dict the notification helper expects.

    Pulls the English source string through gettext under each supported
    locale so parler's per-language rows mirror the .po-file translations.
    ``format_kwargs`` interpolates shared runtime values (order id, etc.)
    into each rendered string.
    """
    languages = [
        lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
    ]
    translations: dict[str, dict[str, str]] = {}
    for language in languages:
        with translation_override(language):
            translations[language] = {
                "title": _(title_msgid).format(**format_kwargs),
                "message": _(message_msgid).format(**format_kwargs),
            }
    return translations


def _load_order(order_id: int) -> Order | None:
    """Fetch an order + its user in a single query, or warn-log and bail.

    Live notifications only make sense for authenticated shoppers — guest
    orders have no ``user`` to address, so we return ``None`` so the
    caller can early-exit without raising (Celery would otherwise retry
    a task that can never succeed).
    """
    try:
        order = Order.objects.select_related("user").get(id=order_id)
    except Order.DoesNotExist:
        logger.warning(
            "Live order notification skipped: order %s not found", order_id
        )
        return None
    if order.user_id is None:
        logger.debug(
            "Live order notification skipped: guest order %s", order_id
        )
        return None
    return order


@shared_task(name="notify_order_created_live", **TASK_DEFAULTS)
def notify_order_created_live(self, order_id: int) -> dict:
    """Fire the initial 'order placed' live notification."""
    order = _load_order(order_id)
    if order is None:
        return {"status": "skipped"}

    create_user_notification(
        order.user,
        kind=NotificationKindEnum.SUCCESS,
        category=NotificationCategoryEnum.ORDER,
        notification_type=NotificationTypeEnum.ORDER_CREATED,
        link=_order_link(order),
        translations=_render_translations(
            "Order #{order_id} placed",
            "Thanks — we've received your order and will keep you posted "
            "as it progresses.",
            order_id=order.id,
        ),
    )
    return {"status": "sent", "order_id": order.id}


@shared_task(name="notify_order_status_changed_live", **TASK_DEFAULTS)
def notify_order_status_changed_live(
    self, order_id: int, new_status: str
) -> dict:
    """Notify about meaningful status transitions.

    Transitions not present in ``_ORDER_STATUS_COPY`` (e.g. PENDING,
    RETURNED, REFUNDED — which each get their own dedicated task or
    signal path) are silently skipped so the caller doesn't need to
    know the allowed set.
    """
    copy = _ORDER_STATUS_COPY.get(new_status)
    if copy is None:
        return {"status": "skipped", "reason": "status_not_notifiable"}

    order = _load_order(order_id)
    if order is None:
        return {"status": "skipped"}

    category, kind, notification_type, (title_msgid, message_msgid) = copy
    create_user_notification(
        order.user,
        kind=kind,
        category=category,
        notification_type=notification_type,
        link=_order_link(order),
        translations=_render_translations(
            title_msgid, message_msgid, order_id=order.id
        ),
    )
    return {"status": "sent", "order_id": order.id, "new_status": new_status}


@shared_task(name="notify_order_shipment_dispatched_live", **TASK_DEFAULTS)
def notify_order_shipment_dispatched_live(self, order_id: int) -> dict:
    """Fire when tracking info is attached (carrier + number present)."""
    order = _load_order(order_id)
    if order is None:
        return {"status": "skipped"}

    create_user_notification(
        order.user,
        kind=NotificationKindEnum.INFO,
        category=NotificationCategoryEnum.SHIPPING,
        notification_type=NotificationTypeEnum.SHIPMENT_DISPATCHED,
        link=_order_link(order),
        translations=_render_translations(
            "Tracking available for order #{order_id}",
            "Your package is being handled by {carrier}. Tracking number: "
            "{tracking_number}.",
            order_id=order.id,
            carrier=order.shipping_carrier or "",
            tracking_number=order.tracking_number or "",
        ),
    )
    return {"status": "sent", "order_id": order.id}


@shared_task(name="notify_payment_confirmed_live", **TASK_DEFAULTS)
def notify_payment_confirmed_live(self, order_id: int) -> dict:
    """Fire once the payment provider confirms the charge."""
    order = _load_order(order_id)
    if order is None:
        return {"status": "skipped"}

    create_user_notification(
        order.user,
        kind=NotificationKindEnum.SUCCESS,
        category=NotificationCategoryEnum.PAYMENT,
        notification_type=NotificationTypeEnum.PAYMENT_CONFIRMED,
        link=_order_link(order),
        translations=_render_translations(
            "Payment confirmed for order #{order_id}",
            "Your payment went through. We're preparing your order now.",
            order_id=order.id,
        ),
    )
    return {"status": "sent", "order_id": order.id}


@shared_task(name="notify_payment_failed_live", **TASK_DEFAULTS)
def notify_payment_failed_live(self, order_id: int) -> dict:
    """Fire when the payment provider rejects the charge."""
    order = _load_order(order_id)
    if order is None:
        return {"status": "skipped"}

    create_user_notification(
        order.user,
        kind=NotificationKindEnum.ERROR,
        category=NotificationCategoryEnum.PAYMENT,
        notification_type=NotificationTypeEnum.PAYMENT_FAILED,
        link=_order_link(order),
        translations=_render_translations(
            "Payment failed for order #{order_id}",
            "We couldn't process your payment. Tap to retry with a "
            "different method.",
            order_id=order.id,
        ),
    )
    return {"status": "sent", "order_id": order.id}


@shared_task(name="notify_order_refunded_live", **TASK_DEFAULTS)
def notify_order_refunded_live(self, order_id: int) -> dict:
    """Fire once a refund has been recorded against the order."""
    order = _load_order(order_id)
    if order is None:
        return {"status": "skipped"}

    create_user_notification(
        order.user,
        kind=NotificationKindEnum.INFO,
        category=NotificationCategoryEnum.PAYMENT,
        notification_type=NotificationTypeEnum.ORDER_REFUNDED,
        link=_order_link(order),
        translations=_render_translations(
            "Refund processed for order #{order_id}",
            "Your refund is on its way back to the original payment "
            "method. It can take 3–5 business days to land.",
            order_id=order.id,
        ),
    )
    return {"status": "sent", "order_id": order.id}
