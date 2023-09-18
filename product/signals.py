from django.db.models.signals import post_save
from django.dispatch import receiver

from product.models.product import Product


@receiver(post_save, sender=Product)
def product_post_save(sender, instance, **kwargs):
    Product.objects.filter(pk=instance.pk).update_search_vector()
    Product.objects.filter(pk=instance.pk).update_calculated_fields()
