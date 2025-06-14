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

    @classmethod
    def get_online_payments(cls) -> list[str]:
        return [
            cls.CREDIT_CARD,
            cls.PAY_PAL,
            cls.STRIPE,
            cls.APPLE_PAY,
            cls.GOOGLE_PAY,
        ]

    @classmethod
    def get_offline_payments(cls) -> list[str]:
        return [
            cls.PAY_ON_DELIVERY,
            cls.PAY_ON_STORE,
            cls.BANK_TRANSFER,
        ]

    @classmethod
    def get_digital_wallet_payments(cls) -> list[str]:
        return [
            cls.APPLE_PAY,
            cls.GOOGLE_PAY,
            cls.PAY_PAL,
        ]
