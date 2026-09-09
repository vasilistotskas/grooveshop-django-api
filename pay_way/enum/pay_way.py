from django.db import models
from django.utils.translation import gettext_lazy as _


class PayWayEnum(models.TextChoices):
    CREDIT_CARD = "CREDIT_CARD", _("Credit Card")
    PAY_ON_DELIVERY = "PAY_ON_DELIVERY", _("Pay On Delivery")
    # BoxNow's own product name, not a description — it is trademarked
    # copy the carrier requires shown verbatim, so the storefront maps
    # this key to the same string in every locale. Distinct from
    # PAY_ON_DELIVERY on purpose: both are collected on delivery, but
    # one is cash to a courier and the other a card at a locker
    # terminal, and a shopper choosing between two buttons both
    # reading "Αντικαταβολή" cannot tell which.
    BOX_NOW_PAY_ON_THE_GO = (
        "BOX_NOW_PAY_ON_THE_GO",
        _("BOX NOW PAY ON THE GO!"),
    )
    PAY_ON_STORE = "PAY_ON_STORE", _("Pay On Store")
    PAY_PAL = "PAY_PAL", _("PayPal")
    STRIPE = "STRIPE", _("Stripe")
    BANK_TRANSFER = "BANK_TRANSFER", _("Bank Transfer")
    APPLE_PAY = "APPLE_PAY", _("Apple Pay")
    GOOGLE_PAY = "GOOGLE_PAY", _("Google Pay")
    VIVA_WALLET = "VIVA_WALLET", _("Viva Wallet")
