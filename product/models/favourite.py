from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class ProductFavourite(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="user_product_favourite",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        "product.Product", related_name="product_favourite", on_delete=models.CASCADE
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Product Favourite")
        verbose_name_plural = _("Product Favourites")
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"], name="unique_product_favourite"
            )
        ]

    def __str__(self):
        return self.user.email
