import logging

import django.dispatch
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from simple_history.signals import post_create_historical_record

from notification.enum import NotificationKindEnum
from notification.models.notification import Notification
from notification.models.user import NotificationUser
from product.models.favourite import ProductFavourite
from product.models.product import Product, ProductTranslation

logger = logging.getLogger(__name__)

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]

product_price_lowered = django.dispatch.Signal()
product_price_increased = django.dispatch.Signal()


@receiver(post_create_historical_record)
def post_create_historical_record_callback(
    sender, instance, history_instance, **kwargs
):
    from order.models.stock_log import StockLog

    prev_record = getattr(history_instance, "prev_record", None)
    if prev_record is None:
        return

    # Track price changes
    old_price = prev_record.price.amount
    new_price = instance.price.amount

    if old_price > new_price:
        product_price_lowered.send(
            sender=Product,
            instance=instance,
            old_price=old_price,
            new_price=new_price,
        )
    elif old_price < new_price:
        product_price_increased.send(
            sender=Product,
            instance=instance,
            old_price=old_price,
            new_price=new_price,
        )

    # Track stock changes
    old_stock = prev_record.stock
    new_stock = instance.stock

    if old_stock != new_stock:
        # Detect if this is a programmatic stock operation from order/stock.py
        # Programmatic operations use save(update_fields=['stock', 'updated_at'])
        # Admin changes typically update multiple fields or use save() without update_fields
        history_change_reason = getattr(
            history_instance, "history_change_reason", None
        )

        # Skip if this is a programmatic stock operation
        # These operations create their own detailed StockLog entries with order context
        if history_change_reason and "StockManager" in history_change_reason:
            return

        quantity_delta = new_stock - old_stock
        operation_type = (
            StockLog.OPERATION_INCREMENT
            if quantity_delta > 0
            else StockLog.OPERATION_DECREMENT
        )

        # Get the user who made the change from history_instance
        performed_by = getattr(history_instance, "history_user", None)

        # Create stock log entry for manual admin changes
        StockLog.objects.create(
            product=instance,
            operation_type=operation_type,
            quantity_delta=quantity_delta,
            stock_before=old_stock,
            stock_after=new_stock,
            reason="Manual stock adjustment via admin panel",
            performed_by=performed_by,
            order=None,
        )


@receiver(post_save, sender=Product)
def reindex_product_translations(sender, instance, **kwargs):
    """
    Reindex all ProductTranslation instances when the master Product is saved.

    This ensures Meilisearch stays in sync when fields on Product change
    (like stock, price, active, etc.) that are indexed via ProductTranslation.
    """
    if settings.MEILISEARCH.get("OFFLINE", False):
        return

    # Get all translations for this product using the optimized queryset
    translations = ProductTranslation.get_meilisearch_queryset().filter(master=instance)

    if not translations.exists():
        return

    # Check if async indexing is enabled
    try:
        from meili.tasks import index_document_task

        celery_available = True
    except ImportError:
        celery_available = False

    use_async = (
        not settings.DEBUG
        and celery_available
        and settings.MEILISEARCH.get("ASYNC_INDEXING", True)
    )

    if use_async:
        # Queue reindex tasks for each translation
        for translation in translations:
            index_document_task.delay(
                app_label="product",
                model_name="producttranslation",
                pk=translation.pk,
            )
    else:
        # Synchronous reindexing for DEBUG mode
        from meili._client import client as _client

        for translation in translations:
            try:
                if not translation.meili_filter():
                    continue

                serialized = translation.meili_serialize()
                pk = translation._meta.pk.value_to_string(translation)

                document = {
                    **serialized,
                    "id": pk,
                    "pk": pk,
                }

                task = _client.get_index(
                    translation._meilisearch["index_name"]
                ).add_documents([document])

                if settings.DEBUG:
                    finished = _client.wait_for_task(task.task_uid)
                    if finished.status == "failed":
                        logger.error(
                            f"Failed to reindex ProductTranslation pk={translation.pk}: {finished.error}"
                        )
            except Exception as e:
                logger.error(
                    f"Error reindexing ProductTranslation pk={translation.pk}: {e}"
                )
                if settings.DEBUG:
                    raise


@receiver(product_price_lowered)
async def notify_product_price_lowered(
    sender, instance, old_price, new_price, **kwargs
):
    favorite_users = await sync_to_async(
        lambda: list(
            ProductFavourite.objects.filter(product=instance).select_related(
                "user"
            )
        )
    )()

    product_url = (
        f"{settings.NUXT_BASE_URL}/products/{instance.id}/{instance.slug}"
    )

    async def get_instance_name():
        return await sync_to_async(
            lambda: instance.safe_translation_getter("name", any_language=True)
            or f"Product {instance.slug or instance.id}",
            thread_sensitive=True,
        )()

    for favorite in favorite_users:
        user = favorite.user

        notification = await Notification.objects.acreate(
            kind=NotificationKindEnum.INFO,
        )

        for language in languages:
            await sync_to_async(notification.set_current_language)(language)
            if language == "en":
                await sync_to_async(setattr)(
                    notification, "title", "Price Drop!"
                )
                name = await get_instance_name()
                await sync_to_async(setattr)(
                    notification,
                    "message",
                    f"The price of <a href='{product_url}'>{name}</a> has dropped"
                    f" from {old_price} to {new_price}. Check it out now!",
                )
            elif language == "el":
                await sync_to_async(setattr)(
                    notification, "title", "Μείωση Τιμής!"
                )
                name = await get_instance_name()
                await sync_to_async(setattr)(
                    notification,
                    "message",
                    f"Η τιμή του <a href='{product_url}'>{name}</a> μειώθηκε"  # noqa: RUF001
                    f" από {old_price} σε {new_price}. Δείτε το τώρα!",
                )
            await notification.asave()

        await NotificationUser.objects.acreate(
            user=user, notification=notification
        )
