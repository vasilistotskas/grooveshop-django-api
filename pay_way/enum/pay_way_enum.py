from django.db import models
from django.utils.translation import gettext_lazy as _


class PayWayEnum(models.TextChoices):
    CREDIT_CARD = "CREDIT_CARD", _("Credit Card")
    PAY_ON_DELIVERY = "PAY_ON_DELIVERY", _("Pay On Delivery")
    PAY_ON_STORE = "PAY_ON_STORE", _("Pay On Store")
    PAY_PAL = "PAY_PAL", _("PayPal")
    STRIPE = "STRIPE", _("Stripe")
    BANK_TRANSFER = "BANK_TRANSFER", _("Bank Transfer")
    APPLE_PAY = "APPLE_PAY", _("Apple Pay")
    GOOGLE_PAY = "GOOGLE_PAY", _("Google Pay")
