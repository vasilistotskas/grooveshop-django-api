from typing import override

from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
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


class ProductReview(TranslatableModel, TimeStampMixinModel, PublishableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "product.Product",
        related_name="reviews",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="product_reviews",
        on_delete=models.CASCADE,
    )
    rate = models.PositiveSmallIntegerField(_("Rate"), choices=RateEnum)
    status = models.CharField(
        _("Status"),
        max_length=250,
        choices=ReviewStatusEnum,
        default=ReviewStatusEnum.NEW,
    )
    translations = TranslatedFields(comment=models.TextField(_("Comment"), blank=True, null=True))

    class Meta(TypedModelMeta):
        verbose_name = _("Product Review")
        verbose_name_plural = _("Product Reviews")
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["product", "user"], name="unique_product_review")]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *PublishableModel.Meta.indexes,
            BTreeIndex(fields=["status"]),
            BTreeIndex(fields=["rate"]),
        ]

    def __unicode__(self):
        comment_snippet = (
            (self.safe_translation_getter("comment", any_language=True)[:50] + "...")
            if self.comment
            else "No Comment"
        )
        return "Review by {0} on {1}: {2}".format(self.user.email, self.product, comment_snippet)

    def __str__(self):
        comment_snippet = (
            (self.safe_translation_getter("comment", any_language=True)[:50] + "...")
            if self.comment
            else "No Comment"
        )
        return f"Review by {self.user.email} on {self.product}: {comment_snippet}"

    @override
    def clean(self):
        if self.rate not in [choice.value for choice in RateEnum]:
            raise ValidationError(_("Invalid rate value."))
        super().clean()
