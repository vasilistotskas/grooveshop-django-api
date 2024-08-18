from django.db.models.signals import post_save
from django.dispatch import receiver

from product.models.product import Product


@receiver(post_save, sender=Product)
def product_post_save(sender, instance, **kwargs):
    # Find the users from ProductFavouriteManager that has this product in favourites,
    # in case the product price has changed to lower than before send a notification to the user
    pass
