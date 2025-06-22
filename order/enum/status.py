from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    PROCESSING = "PROCESSING", _("Processing")
    SHIPPED = "SHIPPED", _("Shipped")
    DELIVERED = "DELIVERED", _("Delivered")
    COMPLETED = "COMPLETED", _("Completed")
    CANCELED = "CANCELED", _("Canceled")
    RETURNED = "RETURNED", _("Returned")
    REFUNDED = "REFUNDED", _("Refunded")

    @classmethod
    def get_active_statuses(cls) -> list["OrderStatus"]:
        return [cls.PENDING, cls.PROCESSING, cls.SHIPPED, cls.DELIVERED]

    @classmethod
    def get_final_statuses(cls) -> list["OrderStatus"]:
        return [cls.COMPLETED, cls.CANCELED, cls.REFUNDED]

    @classmethod
    def get_status_groups(cls) -> dict[str, list["OrderStatus"]]:
        return {
            "active": cls.get_active_statuses(),
            "final": cls.get_final_statuses(),
            "in_transit": [cls.SHIPPED],
            "needs_processing": [cls.PENDING, cls.PROCESSING],
        }


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    PROCESSING = "PROCESSING", _("Processing")
    COMPLETED = "COMPLETED", _("Completed")
    FAILED = "FAILED", _("Failed")
    REFUNDED = "REFUNDED", _("Refunded")
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", _("Partially Refunded")
    CANCELED = "CANCELED", _("Canceled")
