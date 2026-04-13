import logging

import django.dispatch
from django.conf import settings
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from simple_history.signals import post_create_historical_record

from product.models.product import Product, ProductTranslation
from product.models.product_attribute import ProductAttribute

logger = logging.getLogger(__name__)

product_price_lowered = django.dispatch.Signal()
product_price_increased = django.dispatch.Signal()


@receiver(post_create_historical_record)
def post_create_historical_record_callback(
    sender, instance, history_instance, **kwargs
):
    if not isinstance(instance, Product):
        return

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

    update_fields = kwargs.get("update_fields")
    if update_fields and set(update_fields) <= {"view_count"}:
        return

    # Get all translations for this product using the optimized queryset
    translations = ProductTranslation.get_meilisearch_queryset().filter(
        master=instance
    )

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
        logger.debug("Reindexing ProductTranslation Async")
        # Queue reindex tasks for each translation
        for translation in translations:
            index_document_task.delay(
                app_label="product",
                model_name="producttranslation",
                pk=translation.pk,
            )
    else:
        logger.debug("Reindexing ProductTranslation Sync")
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
                    try:
                        finished = _client.client.wait_for_task(
                            task.task_uid, timeout_in_ms=30000
                        )
                        if finished.status == "failed":
                            logger.error(
                                f"Failed to reindex ProductTranslation "
                                f"pk={translation.pk}: {finished.error}"
                            )
                    except Exception as wait_err:
                        logger.warning(
                            f"Meilisearch wait_for_task timed out "
                            f"for pk={translation.pk}: {wait_err}"
                        )
            except Exception as e:
                logger.error(
                    f"Error reindexing ProductTranslation "
                    f"pk={translation.pk}: {e}"
                )


@receiver(product_price_lowered)
def notify_product_price_lowered(
    sender, instance, old_price, new_price, **kwargs
):
    from product.tasks import send_price_drop_notifications

    send_price_drop_notifications.delay(
        product_id=instance.id,
        old_price=float(old_price),
        new_price=float(new_price),
    )


@receiver([post_save, post_delete], sender=ProductAttribute)
def update_product_search_index_on_attribute_change(sender, instance, **kwargs):
    """
    Update search index when product attributes are added, updated, or deleted.

    This ensures Meilisearch stays in sync when product attributes change,
    updating the attributes, attribute_values, attribute_names, and
    attribute_values_text fields in the search index.
    """
    if settings.MEILISEARCH.get("OFFLINE", False):
        return

    # Get all translations for this product using the optimized queryset
    translations = ProductTranslation.get_meilisearch_queryset().filter(
        master=instance.product
    )

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
        logger.debug(
            f"Reindexing ProductTranslation Async due to ProductAttribute change for product {instance.product_id}"
        )
        # Queue reindex tasks for each translation
        for translation in translations:
            index_document_task.delay(
                app_label="product",
                model_name="producttranslation",
                pk=translation.pk,
            )
    else:
        logger.debug(
            f"Reindexing ProductTranslation Sync due to ProductAttribute change for product {instance.product_id}"
        )
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
                    try:
                        finished = _client.client.wait_for_task(
                            task.task_uid, timeout_in_ms=30000
                        )
                        if finished.status == "failed":
                            logger.error(
                                f"Failed to reindex ProductTranslation "
                                f"pk={translation.pk}: {finished.error}"
                            )
                    except Exception as wait_err:
                        logger.warning(
                            f"Meilisearch wait_for_task timed out "
                            f"for pk={translation.pk}: {wait_err}"
                        )
            except Exception as e:
                logger.error(
                    f"Error reindexing ProductTranslation "
                    f"pk={translation.pk}: {e}"
                )
