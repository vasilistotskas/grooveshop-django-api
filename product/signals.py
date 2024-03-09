from django.db.models.signals import post_save
from django.db.models.signals import pre_save
from django.dispatch import receiver
from parler.signals import pre_translation_save

from product.models.product import Product


@receiver(post_save, sender=Product)
def product_post_save(sender, instance, **kwargs):
    Product.objects.filter(pk=instance.pk).update_calculated_fields()


@receiver(pre_save, sender=Product)
def update_search_fields_on_slug_change(sender, instance, **kwargs):
    if instance.pk:
        try:
            original = sender.objects.get(pk=instance.pk)
            if original.slug != instance.slug:
                instance.search_index_dirty = True
                instance.search_document_dirty = True
        except sender.DoesNotExist:
            pass


@receiver(pre_translation_save, sender=Product.translations)
def update_search_fields_on_translation_change(sender, instance, **kwargs):
    if instance.master:
        instance.master.search_index_dirty = True
        instance.master.search_document_dirty = True
        instance.master.save()
