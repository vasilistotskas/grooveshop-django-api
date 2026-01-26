import django.dispatch
from asgiref.sync import sync_to_async
from django.conf import settings
from django.dispatch import receiver
from simple_history.signals import post_create_historical_record

from notification.enum import NotificationKindEnum
from notification.models.notification import Notification
from notification.models.user import NotificationUser
from product.models.favourite import ProductFavourite
from product.models.product import Product

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
