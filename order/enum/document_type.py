from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderDocumentTypeEnum(models.TextChoices):
    RECEIPT = "RECEIPT", _("Receipt")
    INVOICE = "INVOICE", _("Invoice")

    PROFORMA = "PROFORMA", _("Proforma Invoice")
    SHIPPING_LABEL = "SHIPPING_LABEL", _("Shipping Label")
    RETURN_LABEL = "RETURN_LABEL", _("Return Label")
    CREDIT_NOTE = "CREDIT_NOTE", _("Credit Note")

    @classmethod
    def get_document_types_for_status(cls, status: str) -> list[str]:
        from order.enum.status import OrderStatus  # noqa: PLC0415

        if status in [OrderStatus.PENDING, OrderStatus.PROCESSING]:
            return [cls.RECEIPT, cls.INVOICE, cls.PROFORMA]
        elif status in [OrderStatus.SHIPPED, OrderStatus.DELIVERED]:
            return [cls.RECEIPT, cls.INVOICE, cls.SHIPPING_LABEL]
        elif status == OrderStatus.RETURNED:
            return [cls.RECEIPT, cls.INVOICE, cls.RETURN_LABEL]
        elif status == OrderStatus.REFUNDED:
            return [cls.RECEIPT, cls.INVOICE, cls.CREDIT_NOTE]
        else:
            return [cls.RECEIPT, cls.INVOICE]
