import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import models
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from core import celery_app
from core.tasks import MonitoredTask

logger = logging.getLogger(__name__)

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
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
    from notification.enum import NotificationKindEnum
    from notification.models.notification import Notification
    from notification.models.user import NotificationUser
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

    product_url = (
        f"{settings.NUXT_BASE_URL}/products/{product.id}/{product.slug}"
    )
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
        user = favourite.user

        notification = Notification.objects.create(
            kind=NotificationKindEnum.INFO,
        )

        for language in languages:
            notification.set_current_language(language)
            if language == "en":
                notification.title = "Price Drop!"
                notification.message = (
                    f"The price of <a href='{product_url}'>"
                    f"{instance_name}</a> has dropped"
                    f" from {old_price} to {new_price}."
                    f" Check it out now!"
                )
            elif language == "el":
                notification.title = "Μείωση Τιμής!"  # noqa: RUF001
                notification.message = (
                    f"Η τιμή του <a href='{product_url}'>"  # noqa: RUF001
                    f"{instance_name}</a> μειώθηκε"  # noqa: RUF001
                    f" από {old_price} σε {new_price}."
                    f" Δείτε το τώρα!"  # noqa: RUF001
                )
            notification.save()

        NotificationUser.objects.create(user=user, notification=notification)
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


@shared_task
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

    admin_email = getattr(settings, "ADMIN_EMAIL", None) or getattr(
        settings, "INFO_EMAIL", None
    )
    if not admin_email:
        logger.warning(
            "check_low_stock_products: no ADMIN_EMAIL/INFO_EMAIL configured — rolling back claim"
        )
        # Release the claim so a future run with email configured can send.
        Product.objects.filter(id__in=product_ids).update(
            low_stock_alert_sent=False
        )
        return {"alerted": 0, "reason": "no_admin_email"}

    products_to_alert = list(
        Product.objects.filter(id__in=product_ids).prefetch_related(
            "translations"
        )
    )

    rows = [
        {
            "name": p.safe_translation_getter("name", any_language=True)
            or f"Product #{p.id}",
            "sku": getattr(p, "sku", "") or "",
            "stock": p.stock,
            "low_stock_threshold": p.low_stock_threshold,
        }
        for p in products_to_alert
    ]

    context = {
        "products": rows,
        "SITE_NAME": settings.SITE_NAME,
        "INFO_EMAIL": settings.INFO_EMAIL,
        "SITE_URL": settings.NUXT_BASE_URL,
        "STATIC_BASE_URL": settings.STATIC_BASE_URL,
    }
    subject = _("[{site}] Low stock alert — {n} product(s)").format(
        site=settings.SITE_NAME, n=len(rows)
    )
    try:
        text_content = render_to_string(
            "emails/product/low_stock_alert.txt", context
        )
        html_content = render_to_string(
            "emails/product/low_stock_alert.html", context
        )
        msg = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            [admin_email],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()
    except Exception as e:
        logger.error(
            "check_low_stock_products: failed to send alert email: %s",
            e,
            exc_info=True,
        )
        # Release the claim so the next run can retry the send.
        Product.objects.filter(id__in=product_ids).update(
            low_stock_alert_sent=False
        )
        return {"alerted": 0, "error": str(e)}

    logger.info(
        "Low-stock alert email sent for %s product(s): %s",
        len(product_ids),
        product_ids,
    )
    return {"alerted": len(product_ids), "ids": product_ids}
