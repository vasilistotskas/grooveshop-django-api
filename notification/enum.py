from django.db import models
from django.utils.translation import gettext_lazy as _


class NotificationKindEnum(models.TextChoices):
    ERROR = "ERROR", _("Error")
    SUCCESS = "SUCCESS", _("Success")
    INFO = "INFO", _("Info")
    WARNING = "WARNING", _("Warning")
    DANGER = "DANGER", _("Danger")

    @classmethod
    def get_alert_types(cls) -> list[str]:
        return [cls.ERROR, cls.WARNING, cls.DANGER]

    @classmethod
    def get_positive_types(cls) -> list[str]:
        return [cls.SUCCESS, cls.INFO]


class NotificationCategoryEnum(models.TextChoices):
    ORDER = "ORDER", _("Order")
    PAYMENT = "PAYMENT", _("Payment")
    SHIPPING = "SHIPPING", _("Shipping")
    CART = "CART", _("Cart")
    PRODUCT = "PRODUCT", _("Product")
    ACCOUNT = "ACCOUNT", _("Account")
    SECURITY = "SECURITY", _("Security")
    PROMOTION = "PROMOTION", _("Promotion")
    SYSTEM = "SYSTEM", _("System")
    REVIEW = "REVIEW", _("Review")
    WISHLIST = "WISHLIST", _("Wishlist")
    SUPPORT = "SUPPORT", _("Support")
    NEWSLETTER = "NEWSLETTER", _("Newsletter")
    RECOMMENDATION = "RECOMMENDATION", _("Recommendation")


class NotificationPriorityEnum(models.TextChoices):
    LOW = "LOW", _("Low Priority")
    NORMAL = "NORMAL", _("Normal Priority")
    HIGH = "HIGH", _("High Priority")
    URGENT = "URGENT", _("Urgent Priority")
    CRITICAL = "CRITICAL", _("Critical Priority")
