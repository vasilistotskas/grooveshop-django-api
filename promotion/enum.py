from django.db import models
from django.utils.translation import gettext_lazy as _


class PromotionTrigger(models.TextChoices):
    AUTOMATIC = "AUTOMATIC", _("Automatic")
    CODE = "CODE", _("Coupon code")


class BenefitType(models.TextChoices):
    # BUNDLE is deliberately NOT a benefit type: bundles are a catalog
    # construct (a composite product with component stock), not a cart
    # rule — see the promotions design notes.
    PERCENTAGE = "PERCENTAGE", _("Percentage off")
    FIXED_AMOUNT = "FIXED_AMOUNT", _("Fixed amount off")
    FREE_SHIPPING = "FREE_SHIPPING", _("Free shipping")
    BXGY = "BXGY", _("Buy X get Y discounted")
    FREE_GIFT = "FREE_GIFT", _("Free gift item")


class TargetScope(models.TextChoices):
    ORDER = "ORDER", _("Entire order")
    PRODUCTS = "PRODUCTS", _("Specific products")
    CATEGORIES = "CATEGORIES", _("Specific categories")


class CouponRejectionReason(models.TextChoices):
    """Machine-readable reasons a coupon code is refused.

    Values follow the ACP discount-extension error vocabulary
    (schema.discount.json, 2026-01-27) so the agent gateway can pass
    them through verbatim in its follow-up release.
    """

    INVALID = "discount_code_invalid", _("The code does not exist")
    EXPIRED = "discount_code_expired", _("The promotion has ended")
    NOT_STARTED = (
        "discount_code_not_started",
        _("The promotion has not started yet"),
    )
    MINIMUM_NOT_MET = (
        "discount_code_minimum_not_met",
        _("The cart does not meet the minimum subtotal"),
    )
    USAGE_LIMIT_REACHED = (
        "discount_code_usage_limit_reached",
        _("The code has reached its usage limit"),
    )
    COMBINATION_DISALLOWED = (
        "discount_code_combination_disallowed",
        _("The code cannot be combined with the applied promotions"),
    )
    USER_INELIGIBLE = (
        "discount_code_user_ineligible",
        _("The customer is not eligible for this promotion"),
    )
