from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

from core.models import TimeStampMixinModel, UUIDModel


class CustomerGroup(TimeStampMixinModel, UUIDModel):
    """A wholesale segment that prices are attached to.

    Admin-internal label, deliberately NOT parler-translated — customers
    never see the group name outside their own account badge.
    """

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(_("Name"), max_length=100, unique=True)
    discount_percent = models.DecimalField(
        _("Discount percent"),
        max_digits=5,
        decimal_places=2,
        default=Decimal(0),
        validators=[
            MinValueValidator(Decimal(0)),
            MaxValueValidator(Decimal(100)),
        ],
        help_text=_(
            "Percent off the retail NET (VAT-exclusive) price. A fixed "
            "price-list entry for a product wins over this."
        ),
    )
    is_active = models.BooleanField(_("Active"), default=True)
    min_order_value = MoneyField(
        _("Minimum order value"),
        max_digits=11,
        decimal_places=2,
        default=Decimal(0),
        default_currency=settings.DEFAULT_CURRENCY,
        help_text=_(
            "Minimum cart items total (VAT-inclusive) required to place "
            "an order at this group's prices — a standard wholesale "
            "term. 0 disables the check."
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Customer Group")
        verbose_name_plural = _("Customer Groups")
        ordering = ["name"]
        db_table = "b2b_customer_group"
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
        ]

    def __str__(self):
        return self.name
