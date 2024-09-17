from typing import override

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class ProductFavouriteManager(models.Manager):
    def for_user(self, user):
        return self.get_queryset().filter(user=user)

    def for_product(self, product):
        return self.get_queryset().filter(product=product)


class ProductFavourite(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="favourite_products",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        "product.Product",
        related_name="favourited_by",
        on_delete=models.CASCADE,
    )

    objects = ProductFavouriteManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Product Favourite")
        verbose_name_plural = _("Product Favourites")
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["user", "product"], name="unique_product_favourite")]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
        ]

    def __unicode__(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        return f"{self.user.email} - {product_name}"

    def __str__(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        return f"{self.user.email} - {product_name}"

    @override
    def save(self, *args, **kwargs):
        if not self.pk and ProductFavourite.objects.filter(user=self.user, product=self.product).exists():
            raise ValidationError(_("This product is already in the user's favorites."))
        super().save(*args, **kwargs)
