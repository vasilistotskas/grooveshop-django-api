from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import PublishableModel, TimeStampMixinModel, UUIDModel
from product.enum.review import RateEnum, ReviewStatus
from product.managers.review import ProductReviewManager, purchase_items


class ProductReview(
    TranslatableModel, TimeStampMixinModel, PublishableModel, UUIDModel
):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "product.Product",
        related_name="reviews",
        on_delete=models.CASCADE,
        verbose_name=_("Product"),
    )
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="product_reviews",
        on_delete=models.CASCADE,
        verbose_name=_("User"),
    )
    rate = models.PositiveSmallIntegerField(_("Rate"), choices=RateEnum)
    status = models.CharField(
        _("Status"),
        max_length=250,
        choices=ReviewStatus,
        default=ReviewStatus.NEW,
    )
    translations = TranslatedFields(
        comment=models.TextField(_("Comment"), blank=True, null=True)
    )

    objects: ProductReviewManager = ProductReviewManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Product Review")
        verbose_name_plural = _("Product Reviews")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "user"], name="unique_product_review"
            )
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *PublishableModel.Meta.indexes,
            BTreeIndex(fields=["status"], name="product_review_status_ix"),
            BTreeIndex(fields=["rate"], name="product_review_rate_ix"),
            BTreeIndex(fields=["product"], name="product_review_product_ix"),
            BTreeIndex(fields=["user"], name="product_review_user_ix"),
            BTreeIndex(
                fields=["product", "status"],
                name="prod_rev_product_status_ix",
            ),
            BTreeIndex(
                fields=["product", "rate"],
                name="prod_rev_product_rate_ix",
            ),
        ]

    def __str__(self):
        comment_snippet = (
            (
                self.safe_translation_getter("comment", any_language=True)[:50]
                + "..."
            )
            if self.comment
            else gettext("No comment")
        )
        return gettext("Review by %(user)s on %(product)s: %(comment)s") % {
            "user": self.user.email,
            "product": self.product,
            "comment": comment_snippet,
        }

    @property
    def is_verified_purchase(self) -> bool:
        """Whether the author has a COMPLETED order containing the product.

        Reads the ``verified_purchase`` annotation of ``for_list()`` when
        present; a bare instance falls back to one query.
        """
        if "verified_purchase" in self.__dict__:
            return self.__dict__["verified_purchase"]
        return purchase_items(
            user=self.user_id, product=self.product_id
        ).exists()

    def clean(self):
        valid_rates = [choice[0] for choice in RateEnum.choices]
        if self.rate not in valid_rates:
            raise ValidationError(_("Invalid rate value."))
        super().clean()
