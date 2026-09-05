from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.models.validators import MinMoneyValidator

from core.models import TimeStampMixinModel, UUIDModel


class PriceListItem(TimeStampMixinModel, UUIDModel):
    """A fixed per-product NET price for one customer group.

    Wins over the group's ``discount_percent``. Prices here are NET
    (VAT-exclusive), matching ``Product.price`` semantics — VAT is
    applied from the product's own ``vat`` rate at resolution time.
    """

    id = models.BigAutoField(primary_key=True)
    group = models.ForeignKey(
        "b2b.CustomerGroup",
        related_name="price_items",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        "product.Product",
        related_name="b2b_prices",
        on_delete=models.CASCADE,
    )
    net_price = MoneyField(
        _("Net price override"),
        max_digits=11,
        decimal_places=2,
        default_currency=settings.DEFAULT_CURRENCY,
        # The whole resolver works in DEFAULT_CURRENCY: it reads
        # ``product.price.amount`` and stamps the default currency on
        # the result, so an override in another currency would be
        # applied at 1:1. ``settings.CURRENCY_CHOICES`` offers USD, so
        # the admin form did exactly that. Restricting the choices keeps
        # the field honest about what the code can actually honour.
        currency_choices=[
            (settings.DEFAULT_CURRENCY, settings.DEFAULT_CURRENCY)
        ],
        # A negative override is money creation, not a discount: the
        # resolver only clamps final prices from ABOVE (at retail), so
        # a negative net passes straight through into the line total.
        # ``import_price_lines`` already refuses one; the model did not,
        # which left the admin form as the way in.
        validators=[MinMoneyValidator(0)],
        help_text=_(
            "Fixed NET (VAT-exclusive) price for this group. Wins over "
            "the group's discount percent."
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("B2B Price List Item")
        verbose_name_plural = _("B2B Price List Items")
        ordering = ["-created_at"]
        db_table = "b2b_price_list_item"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "product"],
                name="unique_b2b_group_product",
            ),
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["product"], name="b2b_price_product_ix"),
            BTreeIndex(fields=["group"], name="b2b_price_group_ix"),
        ]

    def __str__(self):
        return f"{self.group} → product {self.product_id}: {self.net_price}"
