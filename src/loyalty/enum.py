from django.db import models
from django.utils.translation import gettext_lazy as _


class TransactionType(models.TextChoices):
    EARN = "EARN", _("Earn")
    REDEEM = "REDEEM", _("Redeem")
    EXPIRE = "EXPIRE", _("Expire")
    ADJUST = "ADJUST", _("Adjust")
    BONUS = "BONUS", _("Bonus")


class PriceBasis(models.TextChoices):
    PRICE_EXCL_VAT_NO_DISCOUNT = (
        "price_excl_vat_no_discount",
        _("Price excluding VAT, without discount"),
    )
    PRICE_EXCL_VAT_WITH_DISCOUNT = (
        "price_excl_vat_with_discount",
        _("Discounted price excluding VAT"),
    )
    PRICE_INCL_VAT_NO_DISCOUNT = (
        "price_incl_vat_no_discount",
        _("Price with VAT, without discount"),
    )
    FINAL_PRICE = "final_price", _("Final sale price (with VAT and discount)")
