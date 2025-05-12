from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderStatusEnum(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    PROCESSING = "PROCESSING", _("Processing")
    SHIPPED = "SHIPPED", _("Shipped")
    DELIVERED = "DELIVERED", _("Delivered")
    COMPLETED = "COMPLETED", _("Completed")
    CANCELED = "CANCELED", _("Canceled")
    RETURNED = "RETURNED", _("Returned")
    REFUNDED = "REFUNDED", _("Refunded")

    @classmethod
    def get_active_statuses(cls) -> list["OrderStatusEnum"]:
        return [cls.PENDING, cls.PROCESSING, cls.SHIPPED, cls.DELIVERED]

    @classmethod
    def get_final_statuses(cls) -> list["OrderStatusEnum"]:
        return [cls.COMPLETED, cls.CANCELED, cls.REFUNDED]

    @classmethod
    def get_status_groups(cls) -> dict[str, list["OrderStatusEnum"]]:
        return {
            "active": cls.get_active_statuses(),
            "final": cls.get_final_statuses(),
            "needs_attention": [cls.RETURNED],
            "in_transit": [cls.SHIPPED],
            "needs_processing": [cls.PENDING, cls.PROCESSING],
        }


class PaymentStatusEnum(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    PROCESSING = "PROCESSING", _("Processing")
    COMPLETED = "COMPLETED", _("Completed")
    FAILED = "FAILED", _("Failed")
    REFUNDED = "REFUNDED", _("Refunded")
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", _("Partially Refunded")
    CANCELED = "CANCELED", _("Canceled")
