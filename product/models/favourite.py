from core.models import TimeStampMixinModel
from core.models import UUIDModel
from django.db import models


class ProductFavourite(TimeStampMixinModel, UUIDModel):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount", related_name="product_favourite", on_delete=models.CASCADE
    )
    product = models.ForeignKey(
        "product.Product", related_name="product_favourite", on_delete=models.CASCADE
    )

    class Meta:
        verbose_name_plural = "Favourite Products"
        unique_together = (("user", "product"),)
        ordering = ["-updated_at"]

    def __str__(self):
        return self.user.email

    @property
    def absolute_url(self) -> str:
        return f"//{self.id}/"
