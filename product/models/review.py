from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import PublishableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from product.enum.review import RateEnum
from product.enum.review import ReviewStatusEnum


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
    rate = models.PositiveSmallIntegerField(_("Rate"), choices=RateEnum.choices)
    status = models.CharField(
        _("Status"),
        max_length=250,
        choices=ReviewStatusEnum.choices,
        default=ReviewStatusEnum.NEW,
    )
    translations = TranslatedFields(
        comment=models.CharField(_("Comment"), max_length=250, blank=True, null=True)
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Product Review")
        verbose_name_plural = _("Product Reviews")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "user"], name="unique_product_review"
            )
        ]

    def __unicode__(self):
        return self.safe_translation_getter("comment", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("comment", any_language=True) or ""
