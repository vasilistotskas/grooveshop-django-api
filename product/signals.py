import logging

import django.dispatch
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver
from simple_history.signals import post_create_historical_record

from order.enum.stock_log_reason import StockLogReason
from product.models.product import Product, ProductTranslation
from product.models.product_attribute import ProductAttribute
from tenant.celery import dispatch_on_commit
from vat.models import Vat

logger = logging.getLogger(__name__)

product_price_lowered = django.dispatch.Signal()
product_price_increased = django.dispatch.Signal()
product_back_in_stock = django.dispatch.Signal()


@receiver(
    post_create_historical_record,
    sender=Product.history.model,
    dispatch_uid="product.post_create_historical_record_callback",
)
def post_create_historical_record_callback(
    sender, instance, history_instance, **kwargs
):
    from order.models.stock_log import StockLog

    prev_record = getattr(history_instance, "prev_record", None)
    if prev_record is None:
        return

    # Track price changes using customer-facing price (price minus
    # discount) so a discount-only cut still fires product_price_lowered.
    # Historical records carry ``price`` and ``discount_percent`` but not
    # the vat FK, so we use the live product's vat rate for both sides —
    # the same rate is applied to old and new, so only the base-price /
    # discount delta drives the direction signal.
    def _customer_price(price_amount, discount_pct, vat_rate):
        """price + vat - discount, all as Decimal amounts."""
        vat = price_amount * vat_rate / 100
        discount = price_amount * discount_pct / 100
        return price_amount + vat - discount

    from decimal import Decimal

    vat_rate = instance.vat_percent if instance.vat_id else Decimal(0)

    old_price = _customer_price(
        prev_record.price.amount,
        prev_record.discount_percent,
        vat_rate,
    )
    new_price = _customer_price(
        instance.price.amount,
        instance.discount_percent,
        vat_rate,
    )

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

    # Back-in-stock transition (0 → positive). Emitted before StockLog
    # writes below so subscribers observe the freshly-restocked product.
    if old_stock <= 0 and new_stock > 0:
        product_back_in_stock.send(
            sender=Product,
            instance=instance,
            old_stock=old_stock,
            new_stock=new_stock,
        )

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

        # The staff member who made the change, as the id the history row
        # holds (resolving the user would cost a query to read one number).
        performed_by_staff_id = getattr(
            history_instance, "history_user_id", None
        )

        # Create stock log entry for manual admin changes
        StockLog.objects.create(
            product=instance,
            operation_type=operation_type,
            quantity_delta=quantity_delta,
            stock_before=old_stock,
            stock_after=new_stock,
            reason_code=StockLogReason.MANUAL_ADJUSTMENT,
            # The text the release before reason codes reads; kept until a
            # release that no longer reads it is the only one serving.
            reason="Manual stock adjustment via admin panel",
            performed_by_staff_id=performed_by_staff_id,
            order=None,
        )


def reindex_products_by_pk(product_ids) -> int:
    """Reindex the translations of products changed WITHOUT a save().

    ``queryset.update()`` and ``SoftDeleteQuerySet.delete()`` are single
    SQL statements: they emit no ``post_save``, so
    ``reindex_product_translations`` below never runs and the
    Meilisearch documents keep their old ``active`` / ``is_deleted``
    values. Measured: a bulk update fires **0** post_save receivers
    where an instance save fires 1.

    Both flags are indexed and filtered on — ``search/views.py`` filters
    the INDEXED ``active`` — so bulk-deactivating products left them
    fully searchable and buyable until the nightly sync. The admin
    reported success either way.

    Returns the number of translation documents dispatched.
    """
    if settings.MEILISEARCH.get("OFFLINE", False):
        return 0

    product_ids = list(product_ids)
    if not product_ids:
        return 0

    from meili.tasks import index_document_task

    translation_pks = list(
        ProductTranslation.objects.filter(
            master_id__in=product_ids
        ).values_list("pk", flat=True)
    )
    if not translation_pks:
        return 0

    # Same dispatch shape as ``reindex_product_translations`` below —
    # on commit, by pk, with the task re-loading each row.
    def _dispatch_reindex(pks=translation_pks):
        for pk in pks:
            index_document_task.delay(
                app_label="product",
                model_name="producttranslation",
                pk=pk,
            )

    transaction.on_commit(_dispatch_reindex)
    return len(translation_pks)


@receiver(
    post_save,
    sender=Product,
    dispatch_uid="product.reindex_product_translations",
)
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

    # No import guard: celery is a hard runtime dependency and ``meili``
    # is an installed app, so this import cannot fail. Guarding it meant
    # a genuine breakage would have degraded silently to synchronous
    # indexing instead of surfacing.
    from meili.tasks import index_document_task

    use_async = not settings.DEBUG and settings.MEILISEARCH.get(
        "ASYNC_INDEXING", True
    )

    if use_async:
        # Fetch only PKs from a PLAIN queryset — we dispatch by PK and the
        # async task re-loads each row via get_meilisearch_queryset(). Using
        # the optimized queryset here would run its favourites×reviews
        # aggregate JOIN on every Product.save() just to collect PKs (G0307).
        translation_pks = list(
            ProductTranslation.objects.filter(master=instance).values_list(
                "pk", flat=True
            )
        )
        if not translation_pks:
            return
        logger.debug("Reindexing ProductTranslation Async")

        for pk in translation_pks:
            dispatch_on_commit(
                index_document_task,
                kwargs={
                    "app_label": "product",
                    "model_name": "producttranslation",
                    "pk": pk,
                },
            )
    else:
        # Sync path (DEBUG) serializes each document inline, so it needs the
        # optimized queryset with its counts/prefetches.
        translations = ProductTranslation.get_meilisearch_queryset().filter(
            master=instance
        )
        if not translations.exists():
            return
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
                    type(translation).get_meili_index_name()
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


@receiver(
    product_price_lowered, dispatch_uid="product.notify_product_price_lowered"
)
def notify_product_price_lowered(
    sender, instance, old_price, new_price, **kwargs
):
    from product.tasks import (
        send_price_drop_notifications,
        send_product_alert_price_drop,
    )

    product_id = instance.id
    old_price_f = float(old_price)
    new_price_f = float(new_price)

    dispatch_on_commit(
        send_price_drop_notifications,
        kwargs={
            "product_id": product_id,
            "old_price": old_price_f,
            "new_price": new_price_f,
        },
    )
    dispatch_on_commit(
        send_product_alert_price_drop,
        kwargs={"product_id": product_id, "new_price": new_price_f},
    )


@receiver(
    product_back_in_stock,
    dispatch_uid="product.notify_product_back_in_stock",
)
def notify_product_back_in_stock(sender, instance, **kwargs):
    from product.tasks import (
        notify_back_in_stock_favourites_live,
        send_product_alert_restock,
    )

    product_id = instance.id

    # Explicit opt-in subscribers get an email (ProductAlert RESTOCK).
    dispatch_on_commit(
        send_product_alert_restock, kwargs={"product_id": product_id}
    )
    # Implicit interest (favouriters) gets a live in-app notification.
    dispatch_on_commit(
        notify_back_in_stock_favourites_live, kwargs={"product_id": product_id}
    )


@receiver(
    [post_save, post_delete],
    sender=ProductAttribute,
    dispatch_uid="product.update_product_search_index_on_attribute_change",
)
def update_product_search_index_on_attribute_change(sender, instance, **kwargs):
    """
    Update search index when product attributes are added, updated, or deleted.

    This ensures Meilisearch stays in sync when product attributes change,
    updating the attributes, attribute_values, attribute_names, and
    attribute_values_text fields in the search index.
    """
    if settings.MEILISEARCH.get("OFFLINE", False):
        return

    # No import guard: celery is a hard runtime dependency and ``meili``
    # is an installed app, so this import cannot fail. Guarding it meant
    # a genuine breakage would have degraded silently to synchronous
    # indexing instead of surfacing.
    from meili.tasks import index_document_task

    use_async = not settings.DEBUG and settings.MEILISEARCH.get(
        "ASYNC_INDEXING", True
    )

    if use_async:
        # Fetch only PKs from a PLAIN queryset — see
        # reindex_product_translations above (G0307).
        translation_pks = list(
            ProductTranslation.objects.filter(
                master=instance.product
            ).values_list("pk", flat=True)
        )
        if not translation_pks:
            return
        logger.debug(
            f"Reindexing ProductTranslation Async due to ProductAttribute change for product {instance.product_id}"
        )

        def _dispatch_attr_reindex(pks=translation_pks):
            for pk in pks:
                index_document_task.delay(
                    app_label="product",
                    model_name="producttranslation",
                    pk=pk,
                )

        transaction.on_commit(_dispatch_attr_reindex)
    else:
        # Sync path (DEBUG) serializes inline → needs the optimized queryset.
        translations = ProductTranslation.get_meilisearch_queryset().filter(
            master=instance.product
        )
        if not translations.exists():
            return
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
                    type(translation).get_meili_index_name()
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


@receiver(
    post_save,
    sender=Vat,
    dispatch_uid="product.sync_prices_on_vat_change",
)
def sync_prices_on_vat_change(sender, instance, created, raw=False, **kwargs):
    """A VAT rate edit moves the gross price of every product on it.

    Fans out to the products of the rate in one task, not one save each:
    the price records and the index documents of a large catalogue are
    refreshed in batches.
    """
    from product.tasks import sync_price_records

    if raw or created:
        return
    product_ids = list(
        Product._base_manager.filter(vat=instance).values_list("pk", flat=True)
    )
    if product_ids:
        dispatch_on_commit(
            sync_price_records, kwargs={"product_ids": product_ids}
        )


@receiver(
    pre_delete,
    sender=Vat,
    dispatch_uid="product.remember_vat_products_on_delete",
)
def remember_vat_products_on_delete(sender, instance, **kwargs):
    """Take the rate's product ids while the rows still point at it.

    Deleting a rate leaves its products without VAT (``SET_NULL``), a
    bulk update that saves no Product and leaves no way to find them
    afterwards.
    """
    instance._repriced_product_ids = list(
        Product._base_manager.filter(vat=instance).values_list("pk", flat=True)
    )


@receiver(
    post_delete,
    sender=Vat,
    dispatch_uid="product.sync_prices_on_vat_delete",
)
def sync_prices_on_vat_delete(sender, instance, **kwargs):
    """Re-price the products the deleted rate left without VAT."""
    from product.tasks import sync_price_records

    product_ids = getattr(instance, "_repriced_product_ids", [])
    if product_ids:
        dispatch_on_commit(
            sync_price_records, kwargs={"product_ids": product_ids}
        )
