from django.db import models
from django.utils.translation import gettext_lazy as _
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import PublishableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from product.enum.review import RateEnum
from product.enum.review import StatusEnum


class ProductReview(
    TranslatableModel, TimeStampMixinModel, PublishableModel, UUIDModel
):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "product.Product",
        related_name="product_review_product",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        "user.UserAccount", related_name="product_review_user", on_delete=models.CASCADE
    )
    rate = models.PositiveSmallIntegerField(_("Rate"), choices=RateEnum.choices())
    status = models.CharField(
        _("Status"), max_length=250, choices=StatusEnum.choices(), default="New"
    )
    translations = TranslatedFields(
        comment=models.CharField(_("Comment"), max_length=250, blank=True, null=True)
    )

    class Meta:
        verbose_name = _("Product Review")
        verbose_name_plural = _("Product Reviews")
        ordering = ["-created_at"]

    def __unicode__(self):
        return self.safe_translation_getter("comment", any_language=True)

    def __str__(self):
        return self.safe_translation_getter("comment", any_language=True)
