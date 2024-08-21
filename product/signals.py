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

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]

product_price_lowered = django.dispatch.Signal()
product_price_increased = django.dispatch.Signal()


@receiver(post_create_historical_record)
def post_create_historical_record_callback(sender, instance, history_instance, **kwargs):
    prev_record = getattr(history_instance, "prev_record", None)
    if prev_record is None:
        return

    old_price = prev_record.price.amount
    new_price = instance.price.amount

    if old_price > new_price:
        product_price_lowered.send(sender=Product, instance=instance, old_price=old_price, new_price=new_price)
    elif old_price < new_price:
        product_price_increased.send(sender=Product, instance=instance, old_price=old_price, new_price=new_price)


@receiver(product_price_lowered)
async def notify_product_price_lowered(sender, instance, old_price, new_price, **kwargs):
    favorite_users = await sync_to_async(
        lambda: list(ProductFavourite.objects.filter(product=instance).select_related("user"))
    )()

    product_url = f"{settings.NUXT_BASE_URL}/products/{instance.id}/{instance.slug}"

    for favorite in favorite_users:
        user = favorite.user

        notification = await Notification.objects.acreate(
            kind=NotificationKindEnum.INFO,
        )

        for language in languages:
            await sync_to_async(notification.set_current_language)(language)
            if language == "en":
                await sync_to_async(setattr)(notification, "title", "Price Drop!")
                await sync_to_async(setattr)(
                    notification,
                    "message",
                    f"The price of <a href='{product_url}'>{instance.name}</a> has dropped"
                    f" from {old_price} to {new_price}. Check it out now!",
                )
            elif language == "el":
                await sync_to_async(setattr)(notification, "title", "Μείωση Τιμής!")
                await sync_to_async(setattr)(
                    notification,
                    "message",
                    f"Η τιμή του <a href='{product_url}'>{instance.name}</a> μειώθηκε"
                    f" από {old_price} σε {new_price}. Δείτε το τώρα!",
                )
            await notification.asave()

        await NotificationUser.objects.acreate(user=user, notification=notification)
