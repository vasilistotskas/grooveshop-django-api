from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class ProductFavourite(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount", related_name="product_favourite", on_delete=models.CASCADE
    )
    product = models.ForeignKey(
        "product.Product", related_name="product_favourite", on_delete=models.CASCADE
    )

    class Meta:
        verbose_name = _("Product Favourite")
        verbose_name_plural = _("Product Favourites")
        unique_together = (("user", "product"),)
        ordering = ["-updated_at"]

    def __str__(self):
        return self.user.email
