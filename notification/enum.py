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


class NotificationTypeEnum(models.TextChoices):
    """Catalogue of every fine-grained live notification event.

    ``Notification.notification_type`` is a free-text ``CharField`` on
    the model (left open so ad-hoc admin broadcasts don't require a
    migration), but every event fired from application code goes
    through one of these values. Centralising the set means:

    * dispatchers don't drift (no copy-paste typos in task files),
    * the frontend gets a typed union via OpenAPI regeneration, so
      presentation layers (icon / route mapping) can exhaustively
      switch on it,
    * new event types are added in one place and the change is visible
      in PR review.

    Naming convention is ``<domain>_<event>`` in lower_snake_case so
    values read naturally in logs.
    """

    # Order lifecycle — each fires from ``order/notifications.py``
    # via the corresponding signal handler in ``order/signals/handlers.py``.
    ORDER_CREATED = "order_created", _("Order created")
    ORDER_PROCESSING = "order_processing", _("Order processing")
    ORDER_SHIPPED = "order_shipped", _("Order shipped")
    ORDER_DELIVERED = "order_delivered", _("Order delivered")
    ORDER_COMPLETED = "order_completed", _("Order completed")
    ORDER_CANCELED = "order_canceled", _("Order canceled")
    ORDER_REFUNDED = "order_refunded", _("Order refunded")
    SHIPMENT_DISPATCHED = "shipment_dispatched", _("Shipment dispatched")

    # Payment events — Stripe webhooks.
    PAYMENT_CONFIRMED = "payment_confirmed", _("Payment confirmed")
    PAYMENT_FAILED = "payment_failed", _("Payment failed")

    # Product events — fans out to favouriters, independent of the
    # explicit ProductAlert email subscriptions.
    PRICE_DROP_FAVOURITE = (
        "price_drop_favourite",
        _("Price drop (favourited product)"),
    )
    RESTOCK_FAVOURITE = (
        "restock_favourite",
        _("Back in stock (favourited product)"),
    )

    # Engagement / gamification.
    LOYALTY_TIER_UP = "loyalty_tier_up", _("Loyalty tier promotion")
    COMMENT_LIKED = "comment_liked", _("Blog comment liked")
