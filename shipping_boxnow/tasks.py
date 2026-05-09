from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings
from django.utils import translation

from shipping_boxnow.exceptions import BoxNowAPIError, BoxNowRetryableError
from tenant.credentials import tenant_contact_email, tenant_from_email

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(BoxNowRetryableError, ConnectionError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def create_boxnow_shipment_for_order(self, order_id: int) -> dict[str, Any]:
    """Create a BoxNow delivery-request for an order.

    Idempotent: BoxNowService.create_shipment_for_order returns the
    existing shipment unchanged when delivery_request_id is already set,
    so retrying this task is safe.

    Returns a status dict that Celery stores as the task result.
    Transient (BoxNowRetryableError) failures are automatically retried
    up to 5 times with exponential backoff (max 600s between attempts).
    Business errors (BoxNowAPIError) are logged and returned without
    retrying — they require manual intervention (wrong locker ID, invalid
    phone, etc.).
    """
    # Lazy imports to avoid circular dependencies between shipping_boxnow
    # and order apps at Django startup time.
    from order.models.order import Order
    from shipping_boxnow.services import BoxNowService

    try:
        order = Order.objects.get(id=order_id)
    except Order.DoesNotExist as exc:
        # Defensive retry against the same race the ACS task hit on
        # prod order 47: ``ShippingService.dispatch_create_shipment_task``
        # now wraps in ``transaction.on_commit``, but a stale-replica
        # read can still surface DoesNotExist briefly. Cap at 3
        # attempts so a genuinely-missing order doesn't loop forever.
        if self.request.retries < 3:
            logger.warning(
                "Order %s not yet visible — retrying BoxNow shipment "
                "creation (attempt %s/3)",
                order_id,
                self.request.retries + 1,
            )
            raise self.retry(exc=exc, countdown=5 * (self.request.retries + 1))
        logger.error(
            "Order %s not found for BoxNow shipment creation", order_id
        )
        return {"status": "order_not_found", "order_id": order_id}

    try:
        shipment = BoxNowService.create_shipment_for_order(order)
    except BoxNowAPIError as exc:
        # P-coded business error from BoxNow (e.g. P402 invalid locker,
        # P410 order number conflict). Do not retry — manual intervention
        # is required.
        logger.error(
            "BoxNow business error for order %s: %s",
            order_id,
            exc,
            extra={
                "order_id": order_id,
                "boxnow_code": exc.code,
                "boxnow_status": exc.status_code,
            },
        )
        return {
            "status": "boxnow_api_error",
            "order_id": order_id,
            "code": exc.code,
            "message": str(exc),
        }

    logger.info(
        "BoxNow shipment created for order %s: parcel_id=%s delivery_request_id=%s",
        order_id,
        shipment.parcel_id,
        shipment.delivery_request_id,
        extra={
            "order_id": order_id,
            "parcel_id": shipment.parcel_id,
            "delivery_request_id": shipment.delivery_request_id,
        },
    )
    return {
        "status": "ok",
        "order_id": order_id,
        "parcel_id": shipment.parcel_id,
        "delivery_request_id": shipment.delivery_request_id,
    }


@shared_task(
    bind=True,
    autoretry_for=(BoxNowRetryableError,),
    retry_backoff=True,
    retry_backoff_max=3600,
    max_retries=3,
)
def sync_boxnow_lockers(self) -> dict[str, int]:
    """Refresh the local BoxNowLocker cache from BoxNow's destinations API.

    Scheduled daily via Celery beat (see settings.CELERY_BEAT_SCHEDULE key
    ``sync-boxnow-lockers``). Upserts rows by external_id and marks any
    locker absent from the latest response as is_active=False.
    """
    # Lazy import avoids circular dependency at startup.
    from shipping_boxnow.services import BoxNowService

    result = BoxNowService.sync_lockers()
    # ``extra=result`` would crash because ``result['created']`` collides with
    # ``LogRecord.created`` (built-in timestamp attribute, raises KeyError in
    # ``Logger.makeRecord``). Namespace the counters under a wrapper key.
    logger.info(
        "BoxNow locker sync complete: %s",
        result,
        extra={"counters": result},
    )
    return result


@shared_task(
    bind=True,
    autoretry_for=(BoxNowRetryableError,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=5,
)
def process_boxnow_webhook_event(
    self, envelope: dict[str, Any]
) -> dict[str, Any]:
    """Apply a verified BoxNow webhook envelope asynchronously.

    The HTTP view verifies the HMAC signature synchronously, then
    dispatches the actual state-machine work here so the request
    returns 200 to BoxNow within milliseconds even if the apply path
    needs to chase Celery sub-tasks (arrival emails, order-status
    transitions, etc.). This eliminates Daphne worker saturation
    during BoxNow retry storms.
    """
    from shipping_boxnow.services import BoxNowService

    try:
        event = BoxNowService.apply_webhook_event(envelope)
    except BoxNowRetryableError:
        # Surface to the autoretry decorator above.
        raise
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception(
            "BoxNow webhook apply failed for message %s: %s",
            envelope.get("id", "<unknown>"),
            exc,
        )
        return {
            "status": "error",
            "message_id": envelope.get("id", ""),
            "error": str(exc),
        }

    return {
        "status": "ok",
        "message_id": envelope.get("id", ""),
        "parcel_event_id": getattr(event, "id", None) if event else None,
    }


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
)
def boxnow_send_arrival_notification(
    self, parcel_event_id: int
) -> dict[str, Any]:
    """Notify the customer that their parcel arrived at the BoxNow locker.

    Sends an email (HTML + text) and creates a Notification row (which
    fans out to the WebSocket via the existing post-save signal on
    NotificationUser). Triggered by BoxNowService.apply_webhook_event
    when a ``final-destination`` event is received.

    Both side-effects (email and in-app notification) are attempted; a
    failure in one does not prevent the other. On any exception Celery
    retries the whole task — both calls are idempotent enough for
    duplicate delivery to be acceptable (sending two emails in a crash
    scenario is far better than sending none).
    """
    # All heavy imports are deferred so the task module can be imported
    # cleanly at worker startup without pulling in the full Django ORM
    # graph for shipping_boxnow, order, and notification apps.
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.utils.translation import gettext as _

    from notification.enum import (
        NotificationCategoryEnum,
        NotificationKindEnum,
        NotificationPriorityEnum,
        NotificationTypeEnum,
    )
    from notification.services import create_user_notification
    from shipping_boxnow.models import BoxNowParcelEvent

    try:
        event = BoxNowParcelEvent.objects.select_related(
            "shipment__order", "shipment__locker"
        ).get(id=parcel_event_id)
    except BoxNowParcelEvent.DoesNotExist:
        logger.error(
            "BoxNowParcelEvent %s not found — cannot send arrival notification",
            parcel_event_id,
        )
        # Non-retryable: the event row is absent (was it deleted?).
        # Return a status dict; Celery would retry needlessly on a missing
        # row.
        return {
            "status": "event_not_found",
            "parcel_event_id": parcel_event_id,
        }

    shipment = event.shipment
    order = shipment.order
    locker = shipment.locker
    locker_address = (
        locker.address_line_1
        if locker is not None
        else shipment.locker_external_id
    )

    # --- Email -----------------------------------------------------------
    lang = (
        getattr(order, "language_code", None) or settings.LANGUAGE_CODE or "el"
    )
    with translation.override(lang):
        subject = _("Your BOX NOW parcel arrived at the locker")
        context = {
            "order": order,
            "shipment": shipment,
            "locker": locker,
            "locker_address": locker_address,
            "parcel_id": shipment.parcel_id,
            "SITE_NAME": settings.SITE_NAME,
            "SITE_URL": getattr(settings, "NUXT_BASE_URL", ""),
            "STATIC_BASE_URL": getattr(settings, "STATIC_BASE_URL", ""),
        }
        text_body = render_to_string(
            "emails/order/boxnow_parcel_at_locker.txt", context
        )
        html_body = render_to_string(
            "emails/order/boxnow_parcel_at_locker.html", context
        )

    msg = EmailMultiAlternatives(
        subject=str(subject),
        body=text_body,
        from_email=tenant_from_email(),
        to=[order.email],
        reply_to=[tenant_contact_email() or tenant_from_email()],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)

    logger.info(
        "BoxNow arrival notification email sent for order %s (parcel=%s)",
        order.id,
        shipment.parcel_id,
        extra={"order_id": order.id, "parcel_id": shipment.parcel_id},
    )

    # --- In-app notification (WebSocket fan-out) -------------------------
    # Only possible when the order is linked to a registered user.
    if order.user_id:
        translations: dict[str, dict[str, str]] = {
            "el": {
                "title": "Το πακέτο σας έφτασε!",
                "message": (
                    f"Voucher: {shipment.parcel_id}. Locker: {locker_address}"
                ),
            },
        }
        create_user_notification(
            user=order.user,
            translations=translations,
            kind=NotificationKindEnum.SUCCESS,
            category=NotificationCategoryEnum.SHIPPING,
            priority=NotificationPriorityEnum.HIGH,
            notification_type=NotificationTypeEnum.BOXNOW_PARCEL_AT_LOCKER,
            link=f"/account/orders/{order.id}",
        )

    return {
        "status": "sent",
        "order_id": order.id,
        "parcel_id": shipment.parcel_id,
    }
