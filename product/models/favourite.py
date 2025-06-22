from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel, UUIDModel
from product.managers.favourite import FavouriteManager


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

    objects: FavouriteManager = FavouriteManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Product Favourite")
        verbose_name_plural = _("Product Favourites")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"], name="unique_product_favourite"
            )
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["user"], name="product_favourite_user_ix"),
            BTreeIndex(fields=["product"], name="product_favourite_product_ix"),
        ]

    def __str__(self):
        product_name = self.product.safe_translation_getter(
            "name", any_language=True
        )
        return f"{self.user.email} - {product_name}"

    def save(self, *args, **kwargs):
        if (
            not self.pk
            and ProductFavourite.objects.filter(
                user=self.user, product=self.product
            ).exists()
        ):
            raise ValidationError(
                _("This product is already in the user's favorites.")
            )
        super().save(*args, **kwargs)
