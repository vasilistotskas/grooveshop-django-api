"""Celery tasks for the ACS shipping integration.

Schedule (registered in ``settings.CELERY_BEAT_SCHEDULE``):

* ``sync-acs-stations`` — daily 03:00 Europe/Athens (Phase 2 only).
* ``issue-acs-pickup-list`` — Mon–Fri 16:30 Europe/Athens.
* ``poll-acs-tracking`` — every 15 minutes.

Idempotency:
* ``create_acs_voucher_for_order`` — service method returns the
  existing shipment when ``voucher_no`` is already set.
* ``poll_acs_tracking_one`` — ``AcsTrackingEvent.event_fingerprint``
  unique constraint dedupes events.
* ``issue_daily_acs_pickup_list`` — service method returns ``None``
  when no candidate vouchers exist.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from shipping_acs.exceptions import AcsAPIError, AcsRetryableError

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(AcsRetryableError, ConnectionError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def create_acs_voucher_for_order(self, order_id: int) -> dict[str, Any]:
    """Issue an ACS voucher for ``order_id``."""
    from order.models.order import Order
    from shipping_acs.services import AcsService

    try:
        order = Order.objects.get(id=order_id)
    except Order.DoesNotExist as exc:
        # Defensive retry: the dispatcher already wraps with
        # ``transaction.on_commit``, but a stale-replica read or a
        # connection pool serving an in-flight transaction can still
        # surface DoesNotExist briefly. Without this, prod order 47
        # was permanently marooned in voucher=pending. Cap retries at
        # 3 (~30s total with backoff) so a genuinely-missing order
        # doesn't loop forever.
        if self.request.retries < 3:
            logger.warning(
                "Order %s not yet visible — retrying ACS voucher creation "
                "(attempt %s/3)",
                order_id,
                self.request.retries + 1,
            )
            raise self.retry(exc=exc, countdown=5 * (self.request.retries + 1))
        logger.error("Order %s not found for ACS voucher creation", order_id)
        return {"status": "order_not_found", "order_id": order_id}

    try:
        shipment = AcsService.create_voucher_for_order(order)
    except AcsRetryableError:
        # Transient (HTTP 5xx / 403 / 406 / connection). Re-raise so Celery's
        # autoretry_for handles it — AcsRetryableError subclasses AcsAPIError,
        # so the broader handler below would otherwise swallow it as a
        # permanent business error and the retry policy would be dead code.
        raise
    except AcsAPIError as exc:
        # Business error (bad address, unacceptable destination
        # station, …) — permanent, no retry. Tell a human immediately:
        # the customer has checked out and is waiting (prod order 143
        # stranded invisibly for 10 days on exactly this path).
        from shipping.alerts import alert_admins_shipment_creation_failed

        logger.error(
            "ACS business error for order %s: %s",
            order_id,
            exc,
            extra={
                "order_id": order_id,
                "alias": exc.alias,
                "http_status": exc.http_status,
            },
        )
        alert_admins_shipment_creation_failed(
            order_id=order_id, carrier="ACS", error=str(exc)
        )
        return {
            "status": "acs_api_error",
            "order_id": order_id,
            "message": str(exc),
        }

    logger.info(
        "ACS voucher created for order %s: voucher_no=%s",
        order_id,
        shipment.voucher_no,
        extra={"order_id": order_id, "voucher_no": shipment.voucher_no},
    )
    return {
        "status": "ok",
        "order_id": order_id,
        "voucher_no": shipment.voucher_no,
    }


@shared_task(
    bind=True,
    autoretry_for=(AcsRetryableError,),
    retry_backoff=True,
    retry_backoff_max=3600,
    max_retries=3,
)
def sync_acs_stations(self) -> dict[str, int]:
    """Refresh the local AcsStation cache (Phase 2)."""
    from shipping_acs.services import AcsService

    countries = getattr(settings, "ACS_SUPPORTED_COUNTRIES", ["GR"]) or ["GR"]
    totals = {"upserted": 0, "deactivated": 0}
    for country in countries:
        result = AcsService.sync_stations(country=country)
        for key in totals:
            totals[key] += result.get(key, 0)
    logger.info("ACS station sync complete: %s", totals, extra=totals)
    return totals


@shared_task(
    bind=True,
    autoretry_for=(AcsRetryableError,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
)
def issue_daily_acs_pickup_list(self) -> dict[str, Any]:
    """Issue the day's pickup list via ACS_Issue_Pickup_List."""
    from shipping_acs.services import AcsService

    pickup_list = AcsService.issue_daily_pickup_list()
    if pickup_list is None:
        logger.info("issue_daily_acs_pickup_list: nothing to issue")
        return {"status": "noop"}

    logger.info(
        "ACS pickup list issued: pickup_list_no=%s voucher_count=%s",
        pickup_list.pickup_list_no,
        pickup_list.voucher_count,
    )
    return {
        "status": "ok",
        "pickup_list_no": pickup_list.pickup_list_no,
        "voucher_count": pickup_list.voucher_count,
    }


# Distributed mutex key + TTL for the polling-batch dispatcher.
# TTL is shorter than the 15-minute beat tick so a crashed worker
# can't permanently block the next run — autoexpiry releases the
# lock after 13 minutes if our ``finally`` block didn't fire.
_POLL_BATCH_LOCK_KEY = "acs:poll_batch:lock"
_POLL_BATCH_LOCK_TTL = 13 * 60  # 13 minutes


@shared_task(
    bind=True,
    autoretry_for=(AcsRetryableError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def poll_acs_tracking_batch(self, *, max_per_run: int = 200) -> dict[str, int]:
    """Dispatch per-shipment poll tasks for non-terminal shipments.

    Rate-limit aware: ACS caps at 10 req/sec, so we dispatch sub-tasks
    with staggered ``countdown`` of 0.2s so the natural fan-out hits at
    roughly 5 req/sec — well within the cap with margin for the
    ``tracking_summary`` + ``tracking_details`` two-call pair per
    shipment.

    Concurrency-safe via a Redis-backed ``cache.add`` mutex: with
    ``celery-beat`` enqueuing into a shared RabbitMQ queue and the
    HPA running multiple worker pods, two consumers could otherwise
    each dequeue this beat task and each dispatch the full 200-task
    fan-out — doubling the API rate to 20 req/sec and breaching the
    ACS 10 req/sec cap. The mutex makes the batch single-flight
    cluster-wide.
    """
    from django.core.cache import cache

    from shipping_acs.enum.shipment_state import AcsShipmentState
    from shipping_acs.models import AcsShipment

    # ``cache.add`` is atomic on Redis (SET NX with TTL) — only one
    # worker per cluster wins the lock per beat tick.
    if not cache.add(_POLL_BATCH_LOCK_KEY, 1, _POLL_BATCH_LOCK_TTL):
        logger.info(
            "poll_acs_tracking_batch: another worker holds the lock — "
            "skipping this tick to stay under the ACS 10 req/sec cap."
        )
        return {"dispatched": 0, "skipped": True}

    try:
        cutoff = timezone.now() - timedelta(minutes=15)
        candidates = list(
            AcsShipment.objects.filter(voucher_no__isnull=False)
            .exclude(
                shipment_state__in=[
                    AcsShipmentState.PENDING_CREATION,
                    AcsShipmentState.DELIVERED,
                    AcsShipmentState.RETURNED,
                    AcsShipmentState.CANCELED,
                    AcsShipmentState.LOST,
                ]
            )
            .filter(models_or_null(cutoff))
            .order_by("last_polled_at")
            .values_list("id", flat=True)[:max_per_run]
        )

        for index, shipment_id in enumerate(candidates):
            poll_acs_tracking_one.apply_async(
                args=[shipment_id],
                countdown=index * 0.2,
            )
        return {"dispatched": len(candidates)}
    finally:
        cache.delete(_POLL_BATCH_LOCK_KEY)


def models_or_null(cutoff):
    """Return Q(last_polled_at__lt=cutoff) | Q(last_polled_at__isnull=True)."""
    from django.db.models import Q

    return Q(last_polled_at__lt=cutoff) | Q(last_polled_at__isnull=True)


@shared_task(
    bind=True,
    autoretry_for=(AcsRetryableError, ConnectionError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def poll_acs_tracking_one(self, shipment_id: int) -> dict[str, Any]:
    """Poll a single AcsShipment's tracking details."""
    from shipping_acs.models import AcsShipment
    from shipping_acs.services import AcsService

    try:
        shipment = AcsShipment.objects.select_related("order").get(
            id=shipment_id
        )
    except AcsShipment.DoesNotExist:
        return {"status": "not_found", "shipment_id": shipment_id}

    try:
        shipment = AcsService.poll_shipment_tracking(shipment)
    except AcsRetryableError:
        # Transient — re-raise so autoretry_for retries. AcsRetryableError
        # subclasses AcsAPIError, so the handler below would otherwise treat a
        # retryable 5xx/406 as a permanent poll failure.
        raise
    except AcsAPIError as exc:
        logger.warning(
            "ACS tracking poll failed for shipment=%s: %s",
            shipment_id,
            exc,
        )
        return {
            "status": "acs_api_error",
            "shipment_id": shipment_id,
            "message": str(exc),
        }

    return {
        "status": "ok",
        "shipment_id": shipment_id,
        "shipment_state": shipment.shipment_state,
    }


@shared_task(bind=True)
def check_stale_acs_shipments(self) -> dict[str, Any]:
    """Alert admins about non-terminal shipments with no tracking movement.

    Two staleness classes are reported (real prod cases, 2026-07-11):

    * **Stale tracking** — voucher exists, state non-terminal, and no
      tracking event for ``settings.ACS_STALE_SHIPMENT_DAYS`` days
      (falling back to ``created_at`` when no event was ever
      recorded). Caught: a parcel stuck at the destination station
      after a wrong-address delivery failure; a voucher sitting in
      ``new`` for 50 days because the parcel was never handed over.
    * **Stranded mint** — ``pending_creation`` for over 24 hours.
      Voucher creation normally completes in seconds; a day-old
      pending row means the mint task failed permanently or was lost
      (order 143 stranded 10 days). The immediate mint-failure alert
      (``shipping.alerts``) is the fast path; this digest is the
      backstop for failures where no exception handler ran.

    Mirrors ``product.tasks.check_low_stock_products``: rows are
    claimed atomically (``stale_alert_sent=True``) before the email so
    concurrent runs can't double-send, and the claim is released when
    the email cannot be sent. The flag is re-armed by
    ``AcsService.poll_shipment_tracking`` when a new event arrives, so
    a shipment that moves and stalls again alerts afresh.

    Alerted shipments keep polling — the alert asks a human to either
    chase the parcel with ACS or retire the row via the admin action
    ("Retire selected shipments"), which sets a terminal state and
    thereby stops the poller.
    """
    from django.core.mail import mail_admins
    from django.db.models import Q
    from django.template.loader import render_to_string

    from shipping_acs.enum.shipment_state import AcsShipmentState
    from shipping_acs.models import AcsShipment

    threshold_days = getattr(settings, "ACS_STALE_SHIPMENT_DAYS", 3)
    now = timezone.now()
    cutoff = now - timedelta(days=threshold_days)

    stale_tracking = (
        Q(voucher_no__isnull=False)
        & ~Q(
            shipment_state__in=[
                AcsShipmentState.PENDING_CREATION,
                AcsShipmentState.DELIVERED,
                AcsShipmentState.RETURNED,
                AcsShipmentState.CANCELED,
                AcsShipmentState.LOST,
            ]
        )
        & (
            Q(last_event_at__lt=cutoff)
            | (Q(last_event_at__isnull=True) & Q(created_at__lt=cutoff))
        )
    )
    stranded_mint = Q(
        shipment_state=AcsShipmentState.PENDING_CREATION,
        created_at__lt=now - timedelta(hours=24),
    )

    with transaction.atomic():
        shipment_ids = list(
            AcsShipment.objects.select_for_update(skip_locked=True)
            .filter(stale_alert_sent=False)
            .filter(stale_tracking | stranded_mint)
            .values_list("id", flat=True)
        )
        if not shipment_ids:
            return {"alerted": 0}
        AcsShipment.objects.filter(id__in=shipment_ids).update(
            stale_alert_sent=True
        )

    if not settings.ADMINS:
        logger.warning(
            "check_stale_acs_shipments: no ADMINS configured — "
            "rolling back claim"
        )
        AcsShipment.objects.filter(id__in=shipment_ids).update(
            stale_alert_sent=False
        )
        return {"alerted": 0, "reason": "no_admins"}

    shipments = AcsShipment.objects.filter(id__in=shipment_ids).select_related(
        "order"
    )
    rows = [
        {
            "voucher_no": s.voucher_no or "—",
            "order_id": s.order_id,
            "shipment_state": s.get_shipment_state_display(),
            "last_movement_at": s.last_event_at or s.created_at,
            "days_stale": (now - (s.last_event_at or s.created_at)).days,
        }
        for s in shipments
    ]

    context = {
        "shipments": rows,
        "threshold_days": threshold_days,
        "SITE_NAME": settings.SITE_NAME,
        "INFO_EMAIL": settings.INFO_EMAIL,
        "SITE_URL": settings.NUXT_BASE_URL,
        "STATIC_BASE_URL": settings.STATIC_BASE_URL,
    }
    from django.utils.translation import gettext as _

    subject = _("Stale ACS shipment alert — {n} shipment(s)").format(
        n=len(rows)
    )
    try:
        text_content = render_to_string(
            "emails/shipping_acs/stale_shipments_alert.txt", context
        )
        html_content = render_to_string(
            "emails/shipping_acs/stale_shipments_alert.html", context
        )
        mail_admins(
            subject=subject,
            message=text_content,
            html_message=html_content,
        )
    except Exception as exc:
        logger.error(
            "check_stale_acs_shipments: failed to send alert email: %s",
            exc,
            exc_info=True,
        )
        AcsShipment.objects.filter(id__in=shipment_ids).update(
            stale_alert_sent=False
        )
        return {"alerted": 0, "error": str(exc)}

    logger.info(
        "Stale ACS shipment alert sent for %s shipment(s): %s",
        len(shipment_ids),
        shipment_ids,
    )
    return {"alerted": len(shipment_ids), "ids": shipment_ids}


@shared_task(
    bind=True,
    autoretry_for=(AcsRetryableError,),
    retry_backoff=True,
    retry_backoff_max=3600,
    max_retries=3,
)
def reconcile_acs_cod_payouts(self) -> dict[str, int]:
    """Pull yesterday's COD payouts and upsert AcsCodPayout rows.

    Scheduled daily via Celery beat (``reconcile-acs-cod-payouts``)
    after midnight Athens time so the data set is finalised.
    Idempotent on (voucher_no, cod_payment_date).

    ``cod_payment_date`` defaults to **yesterday** (Athens time) — ACS
    rejects an empty ``COD_Payment_Date`` with ``"Error fill data"`` and
    the beat schedule fires at 02:30 Europe/Athens, by which point
    yesterday's data is finalised on ACS' side.
    """
    from datetime import timedelta

    from django.utils import timezone

    from shipping_acs.services import AcsService

    yesterday = (timezone.localtime() - timedelta(days=1)).date()
    # silent_for_customer: the payment-flip and DELIVERED → COMPLETED
    # advance are internal bookkeeping — the customer already received
    # the DELIVERED notification days earlier and paid the courier in
    # person, so a "completed" email now adds nothing (site-owner
    # decision 2026-07-11: COD reconcile must never email customers).
    result = AcsService.reconcile_cod_payouts(
        cod_payment_date=yesterday, silent_for_customer=True
    )
    # ``extra=result`` would crash because ``result['created'/'updated']``
    # collide with built-in ``LogRecord`` attributes. Namespace under a
    # wrapper key so the structured fields stay queryable.
    logger.info(
        "ACS COD reconciliation complete: %s",
        result,
        extra={"counters": result},
    )
    return result


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
)
def acs_send_arrival_notification(self, shipment_id: int) -> dict[str, Any]:
    """Notify the customer that their parcel is out for delivery.

    Mirrors :func:`shipping_boxnow.tasks.boxnow_send_arrival_notification`
    in shape — emails + in-app notification.  Email templates live at
    ``core/templates/emails/order/acs_out_for_delivery.{html,txt}`` and
    are rendered with the order language; in-app notifications fan out
    through ``notification.services.create_user_notification``.

    Both side-effects are independent: a failure in one does not block
    the other.  On any unexpected exception Celery retries the whole
    task — both calls are idempotent enough for duplicate delivery to
    be acceptable.
    """
    # Heavy imports are deferred so the task module imports cleanly at
    # worker start without pulling in the full ORM graph for
    # shipping_acs, order and notification apps.
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.utils import translation
    from django.utils.translation import gettext as _

    from notification.enum import (
        NotificationCategoryEnum,
        NotificationKindEnum,
        NotificationPriorityEnum,
        NotificationTypeEnum,
    )
    from notification.services import create_user_notification
    from shipping_acs.models import AcsShipment

    try:
        shipment = AcsShipment.objects.select_related("order").get(
            id=shipment_id
        )
    except AcsShipment.DoesNotExist:
        logger.warning(
            "AcsShipment %s not found — cannot send arrival notification",
            shipment_id,
        )
        return {"status": "not_found", "shipment_id": shipment_id}

    order = shipment.order
    lang = (
        getattr(order, "language_code", None) or settings.LANGUAGE_CODE or "el"
    )
    with translation.override(lang):
        subject = _("Your ACS parcel is out for delivery")
        context = {
            "order": order,
            "shipment": shipment,
            "voucher_no": shipment.voucher_no,
            "SITE_NAME": settings.SITE_NAME,
            "SITE_URL": getattr(settings, "NUXT_BASE_URL", ""),
            "STATIC_BASE_URL": getattr(settings, "STATIC_BASE_URL", ""),
        }
        text_body = render_to_string(
            "emails/order/acs_out_for_delivery.txt", context
        )
        html_body = render_to_string(
            "emails/order/acs_out_for_delivery.html", context
        )

    msg = EmailMultiAlternatives(
        subject=str(subject),
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[order.email],
        reply_to=[getattr(settings, "INFO_EMAIL", settings.DEFAULT_FROM_EMAIL)],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)

    if order.user_id:
        translations: dict[str, dict[str, str]] = {
            "el": {
                "title": "Το πακέτο σας είναι καθ' οδόν",
                "message": (
                    f"Voucher: {shipment.voucher_no}. "
                    "Ο μεταφορέας θα επικοινωνήσει σύντομα."
                ),
            },
        }
        # The notification side-effect is best-effort — wrap so a fail
        # there doesn't roll back the email send (already committed).
        try:
            with transaction.atomic():
                create_user_notification(
                    user=order.user,
                    translations=translations,
                    kind=NotificationKindEnum.SUCCESS,
                    category=NotificationCategoryEnum.SHIPPING,
                    priority=NotificationPriorityEnum.HIGH,
                    notification_type=NotificationTypeEnum.ACS_OUT_FOR_DELIVERY,
                    link=f"/account/orders/{order.id}",
                )
        except Exception as exc:
            logger.warning(
                "ACS arrival in-app notification failed for order=%s: %s",
                order.id,
                exc,
            )

    return {
        "status": "sent",
        "order_id": order.id,
        "voucher_no": shipment.voucher_no,
    }
