import logging

from django.conf import settings

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
