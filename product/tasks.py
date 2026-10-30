import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import Truncator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import override as translation_override

from core import celery_app
from core.retry import (
    MAIL_TRANSIENT_ERRORS,
    SEARCH_TRANSIENT_ERRORS,
    TRANSIENT_ERRORS,
)
from core.tasks import MonitoredTask
from core.utils.email_context import build_email_context
from core.utils.i18n import store_language
from core.utils.tenant_urls import storefront_path
from emails.send import Status, send_email, was_sent
from product.emails import ALERTS_GATE, LowStock, PriceDrop

if TYPE_CHECKING:
    from product.models.alert import ProductAlert

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def notify_back_in_stock_favourites_live(product_id: int) -> dict:
    """Send an in-app live notification to users who favourited a product
    when its stock transitions from 0 back to positive.

    Complements the explicit ``send_product_alert_restock`` email task —
    that one emails ProductAlert RESTOCK subscribers (opt-in), this one
    fans out to the broader favouriters list (implicit interest). Users
    who are in both sets will see the live notification + receive the
    email; the two channels are intentionally independent.
    """
    from core.store_settings import store_setting

    if not bool(store_setting("FAVOURITES_ENABLED")):
        return {"status": "skipped", "reason": "favourites_disabled"}

    from notification.enum import (
        NotificationCategoryEnum,
        NotificationKindEnum,
        NotificationTypeEnum,
    )
    from notification.services import create_user_notification
    from product.models.favourite import ProductFavourite
    from product.models.product import Product

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        logger.warning(
            "notify_back_in_stock_favourites_live: product %s not found",
            product_id,
        )
        return {"status": "skipped", "reason": "product_not_found"}

    product_link = storefront_path(product.storefront_path)
    product_name = (
        product.safe_translation_getter("name", any_language=True)
        or f"Product {product.slug or product.id}"
    )

    notified = 0
    favourites_qs = (
        ProductFavourite.objects.filter(product=product)
        .select_related("user")
        .iterator(chunk_size=500)
    )
    for favourite in favourites_qs:
        create_user_notification(
            favourite.user,
            kind=NotificationKindEnum.SUCCESS,
            category=NotificationCategoryEnum.PRODUCT,
            notification_type=NotificationTypeEnum.RESTOCK_FAVOURITE,
            link=product_link,
            # Plain text — the ``link`` field above carries the target
            # URL and the frontend wraps the whole card in a navigate
            # handler, so embedding ``<a>`` tags in the body would just
            # render as literal HTML in the bell and notifications
            # page (both use text interpolation, not v-html).
            translations={
                "en": {
                    "title": "Back in stock",
                    "message": (
                        f"{product_name} is available again. "
                        f"Grab it before it sells out."
                    ),
                },
                "el": {
                    "title": "Ξανά διαθέσιμο",
                    "message": (
                        f"Το {product_name} είναι ξανά διαθέσιμο. "
                        f"Πρόλαβέ το πριν εξαντληθεί."
                    ),
                },
            },
        )
        notified += 1

    logger.info(
        "notify_back_in_stock_favourites_live: notified %s users for product %s",
        notified,
        product_id,
    )
    return {
        "status": "success",
        "product_id": product_id,
        "notified": notified,
    }


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_price_drop_notifications(
    product_id: int,
    old_price: float,
    new_price: float,
) -> dict:
    """
    Notify users who favourited a product that its price has dropped.

    Processes favourite users in batches via iterator() to avoid loading
    the full queryset into memory, and dispatches one notification per user.
    """
    from core.store_settings import store_setting

    if not bool(store_setting("FAVOURITES_ENABLED")):
        return {"status": "skipped", "reason": "favourites_disabled"}

    from notification.enum import (
        NotificationCategoryEnum,
        NotificationKindEnum,
        NotificationTypeEnum,
    )
    from notification.services import create_user_notification
    from product.models.favourite import ProductFavourite
    from product.models.product import Product

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        logger.warning(
            "send_price_drop_notifications: product %s not found, skipping",
            product_id,
        )
        return {"status": "skipped", "reason": "product_not_found"}

    product_link = storefront_path(product.storefront_path)
    instance_name = (
        product.safe_translation_getter("name", any_language=True)
        or f"Product {product.slug or product.id}"
    )

    notified = 0
    favourites_qs = (
        ProductFavourite.objects.filter(product=product)
        .select_related("user")
        .iterator(chunk_size=500)
    )

    for favourite in favourites_qs:
        create_user_notification(
            favourite.user,
            kind=NotificationKindEnum.INFO,
            category=NotificationCategoryEnum.PRODUCT,
            notification_type=NotificationTypeEnum.PRICE_DROP_FAVOURITE,
            link=product_link,
            # Plain text — see restock task above for rationale.
            translations={
                "en": {
                    "title": "Price Drop!",
                    "message": (
                        f"The price of {instance_name} has dropped "
                        f"from {old_price} to {new_price}. "
                        f"Check it out now!"
                    ),
                },
                "el": {
                    "title": "Μείωση Τιμής!",
                    "message": (
                        f"Η τιμή του {instance_name} μειώθηκε από "
                        f"{old_price} σε {new_price}. "
                        f"Δείτε το τώρα!"
                    ),
                },
            },
        )
        notified += 1

    logger.info(
        "Sent price-drop notifications for product %s to %s users",
        product_id,
        notified,
    )
    return {
        "status": "success",
        "product_id": product_id,
        "notified": notified,
    }


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=600,
    time_limit=900,
)
def check_low_stock_products() -> dict:
    """Send a single consolidated low-stock alert to the admin.

    - Triggers per product only once: flips `low_stock_alert_sent=True`
      atomically (row-locked) before sending, preventing duplicate
      sends from concurrent beat executions in HA setups.
    - Automatically clears the flag when stock rises back above the
      threshold.
    - Respects `low_stock_threshold=0` as "disabled for this product".
    """
    from django.db import transaction

    from product.models.product import Product

    # Auto-clear the flag on products whose stock has recovered above
    # the threshold. Runs on every invocation regardless of whether an
    # alert will be sent this time.
    Product.objects.filter(
        low_stock_alert_sent=True,
        stock__gt=models.F("low_stock_threshold"),
    ).update(low_stock_alert_sent=False)

    # Atomically claim the product rows we are about to alert on —
    # any concurrent worker that sees them will get an empty set.
    with transaction.atomic():
        product_ids = list(
            Product.objects.select_for_update(skip_locked=True)
            .filter(
                active=True,
                low_stock_alert_sent=False,
                low_stock_threshold__gt=0,
                stock__lte=models.F("low_stock_threshold"),
            )
            .values_list("id", flat=True)
        )
        if not product_ids:
            return {"alerted": 0}
        Product.objects.filter(id__in=product_ids).update(
            low_stock_alert_sent=True
        )

    products_to_alert = list(
        Product.objects.filter(id__in=product_ids).prefetch_related(
            "translations"
        )
    )

    try:
        deliveries = send_email("team.low_stock", LowStock(products_to_alert))
    except Exception as e:
        logger.exception(
            "check_low_stock_products: failed to send alert email",
        )
        # Release the claim so the next run can retry the send.
        Product.objects.filter(id__in=product_ids).update(
            low_stock_alert_sent=False
        )
        return {"alerted": 0, "error": str(e)}

    if not was_sent(deliveries):
        # Nobody to write to, the type switched off or its feature closed:
        # release the claim so a run after that is fixed can send.
        reason = deliveries[0].status
        logger.warning(
            "check_low_stock_products: not sent (%s) — rolling back claim",
            reason,
        )
        Product.objects.filter(id__in=product_ids).update(
            low_stock_alert_sent=False
        )
        return {"alerted": 0, "reason": reason}

    logger.info(
        "Low-stock alert email sent for %s product(s): %s",
        len(product_ids),
        product_ids,
    )
    return {"alerted": len(product_ids), "ids": product_ids}


def _notify_alerts(alerts, *, type_key: str, wrap=lambda alert: alert):
    """Send the email of *type_key* for each alert; returns the alerts that
    were handled and how many were sent, or ``None`` when the type is off.

    An alert is single-shot: it is handled, sent or not, so a retry loop
    never hammers a broken address. A type that is off (its feature closed,
    or the store switched it off) handles nothing: the alerts stay armed
    for when it is back on.
    """
    handled: list[ProductAlert] = []
    sent = 0
    for alert in alerts:
        try:
            deliveries = send_email(type_key, wrap(alert))
        except Exception:
            logger.exception(
                "Failed to send product alert %s (alert %s)",
                type_key,
                alert.pk,
            )
            handled.append(alert)
            continue
        if deliveries[0].status in (
            Status.SKIPPED_FEATURE_OFF,
            Status.SKIPPED_SWITCHED_OFF,
        ):
            return None
        handled.append(alert)
        sent += was_sent(deliveries)
    return handled, sent


def _with_product(alerts, product):
    """The alerts, each pointing at the one product already loaded."""
    for alert in alerts:
        alert.product = product
        yield alert


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_product_alert_restock(product_id: int) -> dict:
    """Notify every active RESTOCK ProductAlert subscriber for a product."""
    if not ALERTS_GATE.is_open():
        return {"status": "skipped", "reason": "product_alerts_disabled"}

    from product.models.alert import ProductAlert, ProductAlertKind
    from product.models.product import Product

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return {"status": "skipped", "reason": "product_not_found"}

    alerts = ProductAlert.objects.filter(
        product_id=product_id,
        kind=ProductAlertKind.RESTOCK,
        is_active=True,
        notified_at__isnull=True,
    ).select_related("user")

    now = timezone.now()
    outcome = _notify_alerts(
        _with_product(alerts, product), type_key="product.restock_alert"
    )
    if outcome is None:
        return {"status": "skipped", "reason": "switched_off"}
    _handled, sent = outcome

    # Single-shot: deactivate every alert we processed, even failures —
    # a retry loop would otherwise hammer broken addresses.
    ProductAlert.objects.filter(
        product_id=product_id,
        kind=ProductAlertKind.RESTOCK,
        is_active=True,
        notified_at__isnull=True,
    ).update(is_active=False, notified_at=now)

    return {"status": "success", "product_id": product_id, "sent": sent}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_product_alert_price_drop(product_id: int, new_price: float) -> dict:
    """Notify PRICE_DROP subscribers whose target_price is >= new_price."""
    if not ALERTS_GATE.is_open():
        return {"status": "skipped", "reason": "product_alerts_disabled"}

    from product.models.alert import ProductAlert, ProductAlertKind
    from product.models.product import Product

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return {"status": "skipped", "reason": "product_not_found"}

    alerts = ProductAlert.objects.filter(
        product_id=product_id,
        kind=ProductAlertKind.PRICE_DROP,
        is_active=True,
        notified_at__isnull=True,
        target_price__gte=Decimal(str(new_price)),
        target_price_currency=getattr(settings, "DEFAULT_CURRENCY", "EUR"),
    ).select_related("user")

    now = timezone.now()
    outcome = _notify_alerts(
        _with_product(alerts, product),
        type_key="product.price_drop_alert",
        wrap=lambda alert: PriceDrop(alert=alert, new_price=new_price),
    )
    if outcome is None:
        return {"status": "skipped", "reason": "switched_off"}
    handled, sent = outcome

    if handled:
        ProductAlert.objects.filter(id__in=[a.id for a in handled]).update(
            is_active=False, notified_at=now
        )

    return {"status": "success", "product_id": product_id, "sent": sent}


@celery_app.task(
    base=MonitoredTask,
    # The address the visitor typed is the payload; keep it out of the
    # failure log.
    sensitive_kwargs=frozenset({"email"}),
)
def request_guest_product_alert_task(
    *,
    product_id: int,
    kind: str,
    email: str,
    target_price: str | None,
    target_price_currency: str,
    language: str,
) -> dict:
    """Everything a guest's alert request does that depends on the address.

    ``ProductAlertViewSet.create`` only validates the request, then always
    enqueues this one task and answers 202, so neither its answer nor its
    cost tells a new address from a known one. Here: the row lookup,
    (re-)arming with the cooldown, the insert race, and queuing the
    confirmation email (which carries its own retry policy).
    """
    from product.alerts import request_guest_alert
    from tenant.celery import dispatch_on_commit

    alert = request_guest_alert(
        product_id=product_id,
        kind=kind,
        email=email,
        target_price=Decimal(target_price) if target_price else None,
        target_price_currency=target_price_currency,
        language=language,
    )
    if alert is None:
        return {"status": "noop"}
    dispatch_on_commit(
        send_product_alert_confirmation, kwargs={"alert_id": alert.pk}
    )
    return {"status": "armed", "alert_id": alert.pk}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_product_alert_confirmation(alert_id: int) -> dict:
    """Email the double opt-in link of a guest's pending alert. A no-op
    once the alert is confirmed or gone, so a retry is safe."""
    from product.models.alert import ProductAlert

    alert = (
        ProductAlert.objects.select_related("product")
        .filter(pk=alert_id)
        .first()
    )
    if alert is None or not alert.awaiting_confirmation:
        return {"status": "skipped", "reason": "not_pending"}
    if not was_sent(send_email("product.alert_confirmation", alert)):
        return {"status": "skipped", "reason": "not_sent"}
    return {"status": "sent", "alert_id": alert_id}


@celery_app.task(base=MonitoredTask)
def purge_unconfirmed_guest_alerts() -> dict:
    """Delete guest alerts never confirmed within their link's week.
    Registered as a periodic beat task, fanned out per tenant."""
    from product.alerts import purge_unconfirmed_guest_alerts as purge

    deleted = purge()
    logger.info("purge_unconfirmed_guest_alerts: deleted %s row(s)", deleted)
    return {"status": "success", "deleted": deleted}


@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=SEARCH_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def sync_price_records(product_ids: list[int]) -> dict[str, int]:
    """Re-record the consumer price of ``product_ids``, then reindex them.

    Queued when something that is not a Product save moves their price:
    a VAT rate changed or was deleted.
    """
    from promotion.offer_sync import refresh_products

    documents = refresh_products(product_ids)
    return {"products": len(product_ids), "documents": documents}


@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=SEARCH_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def reevaluate_price_episodes() -> dict[str, int]:
    """Reconcile every product's price records with its live price.

    Every path that moves a price records it as it happens (see
    ``product.pricing.service``); this is the net under them. It picks up
    what none of them can see: the merchant's promotions toggle, a
    promotion that was running when this shipped, products saved by a
    release that predates the records. A product already in line costs
    one comparison and writes nothing.
    """
    from product.models.product import Product
    from product.pricing.service import sync_product_prices
    from product.signals import reindex_products_by_pk

    changed = sync_product_prices(
        Product._base_manager.values_list("pk", flat=True)
    )
    reindex_products_by_pk(changed)
    logger.info("Price episode sweep: %s products changed", len(changed))
    return {"products": len(changed)}


#: How much of the comment the staff email quotes; the admin page has it all.
REVIEW_ALERT_COMMENT_CHARS = 300


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_review_pending_alert(review_id: int) -> dict:
    """Email the store's operators that a review waits for approval.

    One mail per entry into ``NEW`` (see
    ``ProductReviewWriteSerializer.save``), through the operational-alert
    gate so the store's ``OPS_ALERT_REVIEW_PENDING_ENABLED`` switch and
    recipients apply. A review that was moderated before this ran is
    skipped: there is nothing left to approve. Rendered in the store's own
    language, not the customer's, because staff read it.
    """
    from django.urls import reverse

    from core.utils.tenant_urls import get_tenant_api_base_url
    from product.enum.review import ReviewStatus
    from product.models.review import ProductReview
    from shipping.alerts import OpsAlertKind, send_ops_alert

    review = (
        ProductReview.objects.with_translations()
        .with_user()
        .with_product()
        .filter(pk=review_id, status=ReviewStatus.NEW)
        .first()
    )
    if review is None:
        return {"status": "skipped", "reason": "not_waiting"}

    language = store_language()
    with translation_override(language):
        product_name = review.product.safe_translation_getter(
            "name", any_language=True
        ) or str(review.product_id)
        comment = review.safe_translation_getter("comment", any_language=True)
        context = build_email_context(
            language=language,
            product_name=product_name,
            stars=review.stars,
            reviewer=review.reviewer_label,
            comment=Truncator(comment or "").chars(REVIEW_ALERT_COMMENT_CHARS),
            admin_url=get_tenant_api_base_url()
            + reverse("admin:product_productreview_change", args=[review.pk]),
        )
        sent = send_ops_alert(
            kind=OpsAlertKind.REVIEW_PENDING,
            subject=_("Review waiting for approval: {product}").format(
                product=product_name
            ),
            message=render_to_string(
                "emails/product/review_pending.txt", context
            ),
            html_message=render_to_string(
                "emails/product/review_pending.html", context
            ),
        )
    return {"status": "sent" if sent else "skipped", "review_id": review.pk}
